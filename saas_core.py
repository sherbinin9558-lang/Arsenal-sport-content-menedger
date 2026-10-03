"""SaaS foundation: Supabase Auth + multi-tenant Postgres via PostgREST."""

import hashlib, json, os, re, time
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests
import streamlit as st
try:
    from streamlit_cookies_controller import CookieController
except Exception:
    CookieController = None

PLAN_LIMITS={"trial":{"products":100,"users":1,"content":100},"starter":{"products":10000,"users":3,"content":1000},"pro":{"products":10000,"users":10,"content":10000},"business":{"products":100000,"users":50,"content":100000}}

def _cfg(name, default=""):
    try: value=st.secrets.get(name, os.getenv(name, default))
    except Exception: value=os.getenv(name, default)
    return str(value or "").strip()

def _supabase_config():
    return _cfg("SUPABASE_URL").rstrip("/"), _cfg("SUPABASE_ANON_KEY")

def saas_enabled():
    url, key = _supabase_config()
    return bool(url and key)

def demo_mode_enabled():
    return _cfg("DEMO_MODE","false").lower() in ("1","true","yes","on")

def _headers(token=None):
    _, key = _supabase_config()
    h={"apikey":key,"Content-Type":"application/json"}
    if token: h["Authorization"]=f"Bearer {token}"
    return h

class SupabaseRequestError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _request(method,path,token=None,**kwargs):
    url, _ = _supabase_config()
    if not url:
        raise SupabaseRequestError("SUPABASE_URL не настроен.")
    extra_headers=kwargs.pop("headers",None) or {}
    headers=_headers(token)
    headers.update(extra_headers)
    r=requests.request(method,f"{url}{path}",headers=headers,timeout=20,**kwargs)
    try: data=r.json()
    except Exception: data={"message":r.text}
    if not r.ok:
        raise SupabaseRequestError(
            data.get("msg") or data.get("message") or data.get("error_description") or str(data),
            r.status_code,
        )
    return data


class DataConflictError(RuntimeError):
    """Raised when another session changed the same SaaS record first."""


_AUTH_COOKIE = "saas_refresh_token"
_AUTH_COOKIE_DAYS = 30

def _auth_cookies():
    if CookieController is None:
        return None
    try:
        if "_saas_cookie_controller" not in st.session_state:
            st.session_state["_saas_cookie_controller"] = CookieController(key="saas_auth_cookie")
        return st.session_state["_saas_cookie_controller"]
    except Exception:
        return None

def _read_refresh_token():
    # CookieController is browser-backed and may need one render cycle after a hard reload.
    # getAll() is more reliable than a single-key read on a fresh Streamlit session.
    cookies = _auth_cookies()
    if cookies is not None:
        try:
            all_cookies = cookies.getAll() or {}
            token = all_cookies.get(_AUTH_COOKIE)
            if token:
                return str(token)
        except Exception:
            pass
        try:
            token = cookies.get(_AUTH_COOKIE)
            if token:
                return str(token)
        except Exception:
            pass
    try:
        token = st.context.cookies.get(_AUTH_COOKIE)
        if token:
            return str(token)
    except Exception:
        pass
    return None

def _persist_refresh_token(refresh_token):
    if not refresh_token:
        return
    cookies = _auth_cookies()
    if cookies is not None:
        try:
            expiry = (datetime.now(timezone.utc) + timedelta(days=_AUTH_COOKIE_DAYS)).isoformat()
            cookies.set(_AUTH_COOKIE, {"value": str(refresh_token), "expiry_date": expiry})
            return
        except Exception:
            try:
                cookies.set(_AUTH_COOKIE, str(refresh_token))
                return
            except Exception:
                pass

def _clear_refresh_token():
    cookies = _auth_cookies()
    if cookies is not None:
        try:
            cookies.remove(_AUTH_COOKIE)
        except Exception:
            pass

def refresh_session(refresh_token):
    return _request("POST","/auth/v1/token?grant_type=refresh_token",json={"refresh_token":refresh_token})

def _establish_session(result):
    token = result.get("access_token")
    if not token:
        raise SupabaseRequestError("Supabase не вернул access token.")
    user = result.get("user") or get_user(token)
    previous_user_id = st.session_state.get("saas_user_id")
    selected_tenant_id = st.session_state.get("saas_tenant_id") if previous_user_id == user.get("id") else None
    tenants = user_tenants(token,user.get("id"))
    tenant = current_tenant(token, user.get("id"), selected_tenant_id)
    st.session_state["saas_access_token"] = token
    st.session_state["saas_user_id"] = user.get("id")
    st.session_state["saas_email"] = user.get("email","")
    if len(tenants) > 1 and not tenant:
        st.session_state["saas_tenant_choices"] = tenants
        st.session_state.pop("saas_tenant_status",None)
        return True
    if not tenant:
        raise RuntimeError("Магазин не найден. Проверьте SaaS SQL-схему.")
    refresh_token = result.get("refresh_token")
    if refresh_token:
        _persist_refresh_token(refresh_token)
    _set_identity(user, tenant)
    st.session_state["saas_last_validated_at"] = time.time()
    st.session_state.pop("_saas_cookie_probe_attempts", None)
    st.session_state.pop("saas_auth_error", None)
    return True

def _restore_session_from_cookie():
    refresh_token = _read_refresh_token()
    # CookieController is asynchronous. On a fresh Streamlit session the first
    # component read can be empty even when the browser already has the cookie.
    # Give it one controlled second chance instead of showing the login screen.
    if not refresh_token:
        attempts = int(st.session_state.get("_saas_cookie_probe_attempts", 0))
        if attempts < 3:
            st.session_state["_saas_cookie_probe_attempts"] = attempts + 1
            time.sleep(0.6)
            refresh_token = _read_refresh_token()
    if not refresh_token:
        return False
    try:
        return _establish_session(refresh_session(refresh_token))
    except SupabaseRequestError as e:
        if e.status_code in (400,401,403):
            _clear_refresh_token()
            return False
        st.session_state["saas_auth_error"] = str(e)
        return False
    except Exception as e:
        st.session_state["saas_auth_error"] = str(e)
        return False

def _restore_session_from_access_token():
    token = st.session_state.get("saas_access_token")
    if not token:
        return False
    last = float(st.session_state.get("saas_last_validated_at", 0) or 0)
    if st.session_state.get("saas_user_id") and (time.time() - last) < 60:
        return True
    try:
        user = get_user(token)
        selected_tenant_id = st.session_state.get("saas_tenant_id")
        tenants = user_tenants(token,user.get("id"))
        tenant = current_tenant(token, user.get("id"), selected_tenant_id)
        if not tenant and len(tenants) > 1:
            st.session_state["saas_user_id"] = user.get("id")
            st.session_state["saas_email"] = user.get("email","")
            st.session_state["saas_tenant_choices"] = tenants
            st.session_state["saas_last_validated_at"] = time.time()
            st.session_state.pop("saas_auth_error", None)
            return True
        if not tenant:
            raise RuntimeError("Магазин не найден.")
        _set_identity(user, tenant)
        st.session_state["saas_last_validated_at"] = time.time()
        st.session_state.pop("saas_auth_error", None)
        return True
    except SupabaseRequestError as e:
        if e.status_code in (400,401,403):
            for k in list(st.session_state):
                if k.startswith("saas_"):
                    del st.session_state[k]
            _clear_refresh_token()
        else:
            st.session_state["saas_auth_error"] = str(e)
        return False
    except Exception as e:
        st.session_state["saas_auth_error"] = str(e)
        return False

def _public_app_url():
    return _cfg("SAAS_PUBLIC_URL","https://arsenal-sport-b3rvpnysmxhvw9wud8wjjd.streamlit.app").rstrip("/")

def sign_up(email,password,store_name):
    payload={"email":email,"password":password,"data":{"store_name":store_name},"redirect_to":_public_app_url()}
    return _request("POST","/auth/v1/signup",json=payload)

def sign_in(email,password):
    return _request("POST","/auth/v1/token?grant_type=password",json={"email":email,"password":password})

def get_user(token): return _request("GET","/auth/v1/user",token=token)

def sign_out(token):
    try: _request("POST","/auth/v1/logout",token=token)
    except Exception: pass
    _clear_refresh_token()

def request_password_reset(email):
    email=str(email or "").strip().lower()
    if not email: raise ValueError("Укажите email.")
    redirect=_public_app_url()
    payload={"email":email,"redirect_to":redirect}
    return _request("POST","/auth/v1/recover",json=payload)

def validate_session():
    return _restore_session_from_access_token() or _restore_session_from_cookie()

def _rest_get(path,token,params=None,headers=None): return _request("GET",path,token=token,headers=headers,params=params or {})

def _rest_get_paged(path, token, params=None):
    _, key = _supabase_config()
    if not key:
        raise SupabaseRequestError("SUPABASE_ANON_KEY не настроен.")
    h = _headers(token)
    h["Prefer"] = "count=exact"
    response = requests.get(
        f"{_supabase_config()[0]}{path}",
        headers=h,
        params=params or {},
        timeout=20,
    )
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text}
    if not response.ok:
        raise SupabaseRequestError(
            data.get("msg") or data.get("message") or data.get("error_description") or str(data),
            response.status_code,
        )
    total = None
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (TypeError, ValueError):
            total = None
    return data, total
def _rest_post(path,token,payload,headers=None): return _request("POST",path,token=token,headers=headers,json=payload)
def _rest_patch(path,token,payload,params=None,headers=None): return _request("PATCH",path,token=token,headers=headers,params=params or {},json=payload)
def _rest_delete(path,token,params=None,headers=None): return _request("DELETE",path,token=token,headers=headers,params=params or {})

def user_tenants(token,user_id=None):
    uid=user_id or st.session_state.get("saas_user_id")
    if not uid: return []
    rows=_rest_get("/rest/v1/memberships",token,params={"select":"tenant_id,role,tenants(id,name,slug,plan,status)","user_id":f"eq.{uid}","order":"created_at.asc"})
    return [row.get("tenants") for row in rows if row.get("tenants")]

def current_tenant(token,user_id=None,selected_tenant_id=None):
    tenants=user_tenants(token,user_id)
    if not tenants:
        return None
    selected=str(selected_tenant_id or st.session_state.get("saas_tenant_id") or "").strip()
    if selected:
        for tenant in tenants:
            if str(tenant.get("id") or "") == selected:
                return tenant
    if len(tenants) == 1:
        return tenants[0]
    return None

def _clear_tenant_runtime_cache():
    for key in list(st.session_state):
        if key.startswith("_saas_data_records_") or key in ("max_data_snapshot","saas_onboarding_complete"):
            st.session_state.pop(key, None)

def switch_tenant(tenant):
    token=st.session_state.get("saas_access_token")
    uid=st.session_state.get("saas_user_id")
    if not token or not uid or not isinstance(tenant,dict) or not tenant.get("id"):
        raise RuntimeError("Не удалось проверить выбранный магазин.")
    verified=current_tenant(token,uid,str(tenant["id"]))
    if not verified:
        raise PermissionError("У вас нет доступа к выбранному магазину.")
    _set_identity({"id":uid,"email":st.session_state.get("saas_email","")},verified)
    _clear_tenant_runtime_cache()
    st.session_state["saas_last_validated_at"]=time.time()

def subscription(token,tenant_id):
    rows=_rest_get("/rest/v1/subscriptions",token,params={"select":"*","tenant_id":f"eq.{tenant_id}","limit":"1"})
    return rows[0] if rows else None

def _set_identity(user,tenant):
    st.session_state["saas_user_id"]=user.get("id")
    st.session_state["saas_email"]=user.get("email","")
    st.session_state["saas_tenant_id"]=tenant.get("id")
    st.session_state["saas_tenant_name"]=tenant.get("name","Магазин")
    st.session_state["saas_plan"]=tenant.get("plan","trial")
    st.session_state["saas_tenant_status"]=tenant.get("status","active")

def supabase_health():
    if not saas_enabled(): return False, "Supabase не настроен: проверьте SUPABASE_URL и SUPABASE_ANON_KEY."
    try:
        _request("GET", "/auth/v1/settings"); return True, "Supabase Auth доступен."
    except Exception as e: return False, f"Supabase недоступен: {e}"

def login_ui():
    st.markdown('<div class="dashboard-hero"><div class="dashboard-hero-kicker">AI BUSINESS PLATFORM</div><div class="dashboard-hero-title">Ваш магазин. Один рабочий центр.</div><div class="dashboard-hero-text">Каталог, контент, заявки, заказы и AI-помощник MAX — в одном месте.</div></div>',unsafe_allow_html=True)
    if saas_enabled():
        ok,message=supabase_health()
        if not ok:
            st.error("Не удалось связаться с сервером аккаунтов."); st.caption(message)
    tab1,tab2,tab3=st.tabs(["Войти","Создать магазин","Восстановить пароль"])
    with tab1:
        email=st.text_input("Email",key="saas_login_email")
        password=st.text_input("Пароль",type="password",key="saas_login_password")
        if st.button("Войти",type="primary",use_container_width=True,key="saas_login"):
            if not email.strip() or not password: st.error("Введите email и пароль.")
            else:
                try:
                    with st.spinner("Проверяем аккаунт…"): result=sign_in(email.strip(),password)
                    _establish_session(result); time.sleep(0.8); st.rerun()
                except Exception as e: st.error(f"Не удалось войти: {e}")
    with tab2:
        st.caption("Стартовая настройка занимает около минуты. После регистрации MAX поможет заполнить магазин.")
        store=st.text_input("Название магазина",placeholder="Например, Demo Store",key="saas_signup_store")
        email=st.text_input("Email владельца",placeholder="you@example.com",key="saas_signup_email")
        password=st.text_input("Пароль",type="password",placeholder="Минимум 8 символов",key="saas_signup_password")
        repeat=st.text_input("Повторите пароль",type="password",key="saas_signup_password2")
        if st.button("Создать магазин и начать",type="primary",use_container_width=True,key="saas_signup"):
            if len(password)<8: st.error("Пароль должен содержать минимум 8 символов.")
            elif password!=repeat: st.error("Пароли не совпадают.")
            elif "@" not in email or "." not in email.split("@")[-1]: st.error("Проверьте email.")
            elif not store.strip(): st.error("Укажите название магазина.")
            else:
                try:
                    with st.spinner("Создаём магазин…"): result=sign_up(email.strip(),password,store.strip())
                    token=result.get("access_token")
                    if not token: st.success("Аккаунт создан. Проверьте почту и подтвердите email. После подтверждения войдите — магазин и тариф Trial создаются автоматически.")
                    else:
                        _establish_session(result); st.rerun()
                except Exception as e: st.error(f"Не удалось создать магазин: {e}")
    with tab3:
        email=st.text_input("Email для восстановления",key="saas_recovery_email")
        st.caption("На почту придёт ссылка для смены пароля.")
        if st.button("Отправить ссылку",type="primary",use_container_width=True,key="saas_recovery"):
            try:
                with st.spinner("Отправляем письмо…"): request_password_reset(email)
                st.success("Если аккаунт существует, письмо для восстановления отправлено.")
            except Exception as e: st.error(f"Не удалось отправить письмо: {e}")

def usage_snapshot():
    return {"products":len(data_load("products", [])),"leads":len(data_load("leads", [])),"orders":len(data_load("orders", [])),"content":len(data_load("content_plan", []))}

def render_tenant_selector():
    token=st.session_state.get("saas_access_token")
    uid=st.session_state.get("saas_user_id")
    if not token or not uid:
        return False
    tenants=user_tenants(token,uid)
    if len(tenants) <= 1:
        st.session_state.pop("saas_tenant_choices",None)
        return False
    current=st.session_state.get("saas_tenant_id")
    labels=[f"{t.get('name','Магазин')} · {str(t.get('slug') or t.get('id',''))[:24]}" for t in tenants]
    by_label=dict(zip(labels,tenants))
    default_index=next((i for i,t in enumerate(tenants) if str(t.get("id"))==str(current)),0)
    st.markdown("### Выберите магазин")
    label=st.selectbox("Магазин",labels,index=default_index,key="saas_tenant_picker")
    if st.button("Открыть магазин",type="primary",use_container_width=True,key="saas_tenant_switch"):
        try:
            switch_tenant(by_label[label])
            st.session_state.pop("saas_tenant_choices",None)
            st.rerun()
        except Exception as e:
            st.error(f"Не удалось переключить магазин: {e}")
    return True

def render_account_bar():
    with st.sidebar:
        st.markdown("---")
        token=st.session_state.get("saas_access_token")
        uid=st.session_state.get("saas_user_id")
        if token and uid:
            try:
                tenants=user_tenants(token,uid)
                if len(tenants)>1:
                    labels=[f"{t.get('name','Магазин')} · {str(t.get('slug') or t.get('id',''))[:24]}" for t in tenants]
                    current=str(st.session_state.get("saas_tenant_id") or "")
                    idx=next((i for i,t in enumerate(tenants) if str(t.get("id"))==current),0)
                    choice=st.selectbox("Магазин",labels,index=idx,key="saas_account_tenant_picker")
                    if st.button("Переключить магазин",use_container_width=True,key="saas_account_tenant_switch"):
                        try:
                            selected=tenants[labels.index(choice)]
                            switch_tenant(selected)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Не удалось переключить магазин: {e}")
            except Exception as e:
                st.caption(f"Не удалось загрузить список магазинов: {e}")
        st.caption(f"Магазин · {st.session_state.get('saas_tenant_name','')}")
        plan=str(st.session_state.get('saas_plan','trial')).lower(); st.caption(f"Тариф · {plan.upper()}"); st.caption(f"Роль · {current_role().upper()}")
        if plan in PLAN_LIMITS:
            u=usage_snapshot(); max_products=PLAN_LIMITS[plan]["products"]; st.progress(min(1.0,u["products"]/max_products),text=f"Каталог · {u['products']} / {max_products}")
        if saas_enabled():
            try:
                for inv in my_invitations():
                    st.info(f"Приглашение: роль «{inv.get('role')}»")
                    if st.button("Принять приглашение",key=f"accept_inv_{inv.get('id')}",use_container_width=True):
                        accept_invitation(inv.get("id")); st.success("Приглашение принято."); st.rerun()
            except Exception: pass
        if st.button("Выйти",key="saas_logout",use_container_width=True):
            token=st.session_state.get("saas_access_token")
            if token: sign_out(token)
            for k in list(st.session_state):
                if k.startswith("saas_"): del st.session_state[k]
            st.rerun()

def plan_catalog():
    return {"starter":{"name":"STARTER","products":10000,"users":3,"description":"Для небольшого магазина"},"pro":{"name":"PRO","products":10000,"users":10,"description":"Для растущего бизнеса"},"business":{"name":"BUSINESS","products":100000,"users":50,"description":"Для сети и большого каталога"}}

def _service_key():
    try: value=st.secrets.get("SUPABASE_SERVICE_ROLE_KEY",os.getenv("SUPABASE_SERVICE_ROLE_KEY",""))
    except Exception: value=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
    return str(value or "").strip()

def activate_paid_subscription(plan,provider_payment_id,payment_method_id=None,provider="yookassa",tenant=None):
    key=_service_key()
    if not key or not saas_enabled():
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY не настроен.")
    if plan not in ("starter","pro","business"):
        raise ValueError("Недопустимый тариф.")
    if not provider_payment_id:
        raise ValueError("provider_payment_id обязателен.")
    tid=str(tenant or tenant_id())
    base=f"{_supabase_config()[0]}/rest/v1"
    headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    response=requests.post(
        f"{base}/rpc/activate_paid_subscription",
        headers=headers,
        json={
            "p_tenant_id": tid,
            "p_plan": plan,
            "p_provider": str(provider),
            "p_provider_payment_id": str(provider_payment_id),
            "p_provider_payment_method_id": str(payment_method_id) if payment_method_id else None,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(response.text)
    st.session_state["saas_plan"]=plan
    st.session_state["saas_tenant_status"]="active"
    return True

def set_auto_renew(enabled):
    tid=tenant_id()
    if not saas_enabled(): st.session_state["auto_renew"]=bool(enabled); return True
    if current_role() not in ("owner","admin"): raise PermissionError("Только владелец или администратор может менять автопродление.")
    key=_service_key()
    if not key: raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY не настроен.")
    headers={"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Prefer":"return=minimal"}; base=f"{_supabase_config()[0]}/rest/v1"
    rr=requests.patch(f"{base}/subscriptions",headers=headers,params={"tenant_id":f"eq.{tid}"},json={"auto_renew":bool(enabled),"cancel_at_period_end":not bool(enabled)},timeout=20)
    if not rr.ok: raise RuntimeError(rr.text)
    st.session_state["auto_renew"]=bool(enabled); return True

def subscription_snapshot():
    token=st.session_state.get("saas_access_token"); tid=tenant_id()
    if not saas_enabled() or not token: return {"plan":tenant_plan(),"status":"trialing","provider":None,"current_period_end":None,"auto_renew":False,"cancel_at_period_end":False,"next_billing_at":None}
    try:
        row=subscription(token,tid) or {}
        return {"plan":row.get("plan",tenant_plan()),"status":row.get("status","trialing"),"provider":row.get("provider"),"current_period_end":row.get("current_period_end"),"auto_renew":bool(row.get("auto_renew",False)),"cancel_at_period_end":bool(row.get("cancel_at_period_end",False)),"next_billing_at":row.get("next_billing_at")}
    except Exception: return {"plan":tenant_plan(),"status":"unknown","provider":None,"current_period_end":None,"auto_renew":False,"cancel_at_period_end":False,"next_billing_at":None}

def request_plan_change(plan):
    if plan not in ("starter","pro","business"): raise ValueError("Недопустимый тариф.")
    st.session_state["requested_plan"]=plan; return True

def current_role(token=None):
    if not saas_enabled(): return "owner"
    token=token or st.session_state.get("saas_access_token"); uid=st.session_state.get("saas_user_id"); tid=tenant_id()
    if not token or not uid or not tid: return "viewer"
    rows=_rest_get("/rest/v1/memberships",token,params={"select":"role","tenant_id":f"eq.{tid}","user_id":f"eq.{uid}","limit":"1"})
    return rows[0].get("role","viewer") if rows else "viewer"

def team_members():
    token=st.session_state.get("saas_access_token")
    if not saas_enabled() or not token: return [{"user_id":st.session_state.get("saas_user_id","demo-user"),"role":"owner"}]
    return _rest_get("/rest/v1/memberships",token,params={"select":"user_id,role,created_at","tenant_id":f"eq.{tenant_id()}","order":"created_at.asc"})

def my_invitations():
    token=st.session_state.get("saas_access_token")
    if not saas_enabled() or not token or not st.session_state.get("saas_email"): return []
    return _rest_get("/rest/v1/invitations",token,params={"select":"id,email,role,status,expires_at","email":f"eq.{st.session_state.get('saas_email').lower()}","status":"eq.pending","order":"created_at.desc"})

def accept_invitation(invite_id):
    token=st.session_state.get("saas_access_token")
    if not token: raise RuntimeError("Нужно войти в аккаунт.")
    return _request("POST","/rest/v1/rpc/accept_invitation",token=token,json={"invite_id":invite_id})

def team_invitations():
    token=st.session_state.get("saas_access_token")
    if not saas_enabled() or not token: return []
    return _rest_get("/rest/v1/invitations",token,params={"select":"id,email,role,status,created_at,expires_at","tenant_id":f"eq.{tenant_id()}","status":"eq.pending","order":"created_at.desc"})

def can(action):
    role=current_role()
    if role=="owner": return True
    if action=="read": return role in ("admin","manager","editor","viewer")
    if action=="write_data": return role in ("admin","manager","editor")
    if action in ("billing","team","settings","manage_roles"): return role=="admin"
    return False

def set_member_role(target_user,new_role):
    token=st.session_state.get("saas_access_token")
    if not saas_enabled() or not token: return False
    if not can("manage_roles"): raise PermissionError("Только владелец или администратор может менять роли.")
    if new_role not in ("admin","manager","editor","viewer"): raise ValueError("Недопустимая роль.")
    return _request("POST","/rest/v1/rpc/set_member_role",token=token,json={"target_tenant":tenant_id(),"target_user":target_user,"new_role":new_role})

def create_team_invitation(email,role):
    token=st.session_state.get("saas_access_token")
    if not saas_enabled() or not token: return None
    if not can("team"): raise PermissionError("Только администратор или владелец может приглашать участников.")
    role=str(role or "viewer").lower()
    if role not in ("admin","manager","editor","viewer"): raise ValueError("Недопустимая роль.")
    members=team_members(); pending=team_invitations(); max_users=limit("users")
    if max_users and len(members)+len(pending)>=max_users: raise ValueError(f"Лимит участников тарифа {tenant_plan().upper()} достигнут.")
    email=email.strip().lower()
    if not email: raise ValueError("Укажите email.")
    return _rest_post("/rest/v1/invitations",token,{"tenant_id":tenant_id(),"email":email,"role":role,"invited_by":st.session_state.get("saas_user_id")})

def onboarding_complete():
    if not saas_enabled(): return True
    settings=data_load("settings", [])
    return bool(settings and isinstance(settings[0],dict) and settings[0].get("onboarding_complete"))

def render_onboarding():
    st.markdown('<div class="dashboard-hero"><div class="dashboard-hero-kicker">QUICK START</div><div class="dashboard-hero-title">Настроим магазин за 60 секунд</div><div class="dashboard-hero-text">Эти данные нужны MAX, чтобы писать контент, отвечать клиентам и подсказывать следующие действия. Их можно изменить позже.</div></div>',unsafe_allow_html=True)
    with st.form("saas_onboarding_form"):
        business=st.selectbox("Что продаёте?",["Спортивный магазин","Одежда и обувь","Интернет-магазин","Другое"],key="onb_business")
        city=st.text_input("Город / регион",placeholder="Краснодарский край",key="onb_city")
        telegram=st.text_input("Telegram магазина",placeholder="@your_store",key="onb_telegram")
        instagram=st.text_input("Instagram",placeholder="@your_store",key="onb_instagram")
        shipping=st.selectbox("Доставка",["По России","По региону","Самовывоз","Другое"],key="onb_shipping")
        goal=st.selectbox("Главная цель",["Больше продаж","Больше заявок","Регулярный контент","Порядок в каталоге"],key="onb_goal")
        positioning=st.text_area("Чем магазин отличается?",placeholder="Например: большой выбор футбольной экипировки и быстрая доставка.",height=90,key="onb_positioning")
        submitted=st.form_submit_button("Сохранить и открыть MAX",type="primary",use_container_width=True)
    if submitted:
        try:
            onboarding_payload={"business_type":business,"city":city.strip(),"telegram":telegram.strip(),"instagram":instagram.strip(),"shipping":shipping,"goal":goal,"positioning":positioning.strip(),"onboarding_complete":True}
            existing=data_load("settings",[])
            if existing and isinstance(existing[0],dict) and existing[0].get("_saas_record_id"):
                onboarding_payload["_saas_record_id"]=existing[0]["_saas_record_id"]
            data_save("settings",[onboarding_payload])
            st.session_state["saas_onboarding_complete"]=True; st.success("Магазин настроен. MAX готов к работе."); st.rerun()
        except Exception as e: st.error(f"Не удалось сохранить настройки магазина: {e}")

def require_saas_access():
    if not saas_enabled():
        if demo_mode_enabled():
            if not st.session_state.get("saas_demo"):
                st.session_state["saas_demo"]=True
                demo={"id":"demo-user","email":"demo@example.com","tenant_id":"demo-tenant","tenant_name":"Demo Store","plan":"pro","status":"active"}
                _set_identity(demo,{"id":"demo-tenant","name":"Demo Store","plan":"pro","status":"active"})
            render_account_bar()
            return True
        st.markdown('<div class="dashboard-hero"><div class="dashboard-hero-kicker">PRODUCTION SETUP</div><div class="dashboard-hero-title">Подключите Supabase, чтобы открыть платформу.</div><div class="dashboard-hero-text">Для коммерческого режима нужны SUPABASE_URL и SUPABASE_ANON_KEY. После подключения пользователи смогут регистрировать магазины, входить по паролю и работать изолированно по tenant.</div></div>',unsafe_allow_html=True)
        st.error("Платформа не запускается в демо-режиме. Добавьте Supabase Secrets в настройках Streamlit.")
        return False

    if not st.session_state.get("saas_access_token"):
        if not _restore_session_from_cookie():
            if st.session_state.get("saas_auth_error"):
                st.error("Не удалось восстановить сессию. Проверьте соединение с сервером аккаунтов и обновите страницу.")
                st.caption(st.session_state["saas_auth_error"])
            login_ui()
            return False

    if not _restore_session_from_access_token():
        if st.session_state.get("saas_auth_error"):
            st.error("Сервер аккаунтов временно недоступен. Текущая авторизация не была удалена.")
            st.caption(st.session_state["saas_auth_error"])
            return False
        login_ui()
        return False

    st.session_state.pop("saas_auth_error", None)

    if not st.session_state.get("saas_tenant_id"):
        if render_tenant_selector():
            return False
        st.error("Магазин не выбран.")
        return False

    tenant_status = str(st.session_state.get("saas_tenant_status", "active")).lower()
    if tenant_status != "active":
        if tenant_status == "suspended":
            st.error("Магазин временно приостановлен. Обратитесь к администратору платформы.")
        elif tenant_status == "cancelled":
            st.error("Магазин закрыт. Доступ к рабочему пространству отключён.")
        else:
            st.error(f"Доступ к магазину ограничен: статус {tenant_status}.")
        return False

    if not onboarding_complete() and not st.session_state.get("saas_onboarding_complete"):
        render_onboarding()
        return False

    render_account_bar()
    return True

def tenant_id(): return st.session_state.get("saas_tenant_id","demo-tenant")
def tenant_plan(): return st.session_state.get("saas_plan","trial")
def limit(name): return PLAN_LIMITS.get(tenant_plan(),PLAN_LIMITS["trial"]).get(name,0)
def feature_allowed(name,current_count=0):
    maximum=limit(name); return maximum<=0 or current_count<maximum

def ensure_can_create(entity,current_count=0):
    if not can("write_data"): raise PermissionError("У вашей роли нет прав на изменение данных магазина.")
    if not feature_allowed(entity,current_count): raise ValueError(f"Лимит {entity} тарифа {tenant_plan().upper()} достигнут.")
    return True

def _data_session_key(entity):
    return f"_saas_data_records_{entity}"


def _new_record_id():
    return hashlib.sha256(
        f"{time.time_ns()}:{os.urandom(16).hex()}".encode("utf-8")
    ).hexdigest()[:24]


_INTERNAL_RECORD_KEY = "_saas_record_id"


def _clean_payload(row):
    if not isinstance(row, dict):
        return row
    return {k: v for k, v in row.items() if k not in {_INTERNAL_RECORD_KEY, "_saas_updated_at"}}


def _prepare_loaded_payload(row):
    payload = dict(row.get("payload") or {})
    payload[_INTERNAL_RECORD_KEY] = str(row.get("record_id"))
    if row.get("updated_at"):
        payload["_saas_updated_at"] = row.get("updated_at")
    return payload


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")


def _validate_data_entity(entity):
    entity = str(entity or "").strip()
    if not entity or not entity.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Недопустимое имя сущности данных.")
    return entity


def data_load(entity,default):
    entity = _validate_data_entity(entity)
    if not saas_enabled():
        path=f"data/{tenant_id()}_{entity}.json"
        try:
            with open(path,encoding="utf-8") as f: return json.load(f)
        except Exception:
            # Production/demo tenants must never fall back to shared legacy files.
            # A tenant-specific local file is the only non-Supabase fallback.
            return default

    token=st.session_state.get("saas_access_token")
    rows=_rest_get(
        "/rest/v1/app_data",
        token,
        params={
            "select":"record_id,payload,created_at,updated_at",
            "tenant_id":f"eq.{tenant_id()}",
            "entity":f"eq.{entity}",
            "order":"created_at.asc,record_id.asc",
        },
    )
    if not rows:
        st.session_state[_data_session_key(entity)] = {}
        return default

    baseline = {}
    loaded = []
    for row in rows:
        record_id = str(row.get("record_id"))
        baseline[record_id] = {
            "updated_at": row.get("updated_at"),
            "created_at": row.get("created_at"),
            "payload": dict(row.get("payload") or {}),
        }
        loaded.append(_prepare_loaded_payload(row))

    st.session_state[_data_session_key(entity)] = baseline
    return loaded


def data_load_page(entity, page=1, page_size=50, search="", category="Все"):
    """Load one page only; designed for catalogs with thousands of records."""
    entity = _validate_data_entity(entity)
    page = max(1, int(page or 1))
    page_size = max(10, min(100, int(page_size or 50)))
    search = str(search or "").strip()
    category = str(category or "Все").strip()

    if not saas_enabled():
        rows = data_load(entity, [])
        q = search.lower()
        if q:
            rows = [r for r in rows if q in " ".join(str(r.get(k, "")) for k in ("name", "brand", "article")).lower()]
        if category and category != "Все":
            rows = [r for r in rows if r.get("category", "Другое") == category]
        total = len(rows)
        start = (page - 1) * page_size
        return {"rows": rows[start:start + page_size], "total": total, "page": page, "page_size": page_size}

    token = st.session_state.get("saas_access_token")
    params = {
        "select": "record_id,payload,created_at,updated_at",
        "tenant_id": f"eq.{tenant_id()}",
        "entity": f"eq.{entity}",
        "order": "created_at.desc,record_id.desc",
        "limit": str(page_size),
        "offset": str((page - 1) * page_size),
    }
    if search:
        q = re.sub(r"[*(),]", " ", search).strip()
        if q:
            params["or"] = f"(payload->>name.ilike.*{q}*,payload->>brand.ilike.*{q}*,payload->>article.ilike.*{q}*)"
    if category and category != "Все":
        params["payload->>category"] = f"eq.{category}"

    rows, total = _rest_get_paged("/rest/v1/app_data", token, params=params)
    prepared = [_prepare_loaded_payload(row) for row in rows]
    return {"rows": prepared, "total": int(total if total is not None else len(prepared)), "page": page, "page_size": page_size}


def data_update_record(entity, record_id, payload, expected_updated_at):
    """Atomically update one record without rewriting the whole entity."""
    entity = _validate_data_entity(entity)
    if not record_id or not expected_updated_at:
        raise DataConflictError("Не удалось проверить версию записи. Обновите страницу и повторите.")
    if saas_enabled() and not can("write_data"):
        raise PermissionError("У вашей роли нет прав на изменение данных магазина.")
    try:
        _rest_post("/rest/v1/rpc/save_app_data_batch", st.session_state.get("saas_access_token"), {
            "p_tenant_id": tenant_id(), "p_entity": entity,
            "p_rows": [{"record_id": str(record_id), "payload": _clean_payload(payload),
                        "expected_updated_at": expected_updated_at, "is_new": False, "is_deleted": False}],
        })
    except SupabaseRequestError as e:
        if e.status_code in (400, 409) and any(x in str(e) for x in ("DATA_CONFLICT", "RECORD_NOT_FOUND")):
            raise DataConflictError("Данные изменились в другой сессии. Обновите страницу и повторите.")
        raise


def data_delete_record(entity, record_id, expected_updated_at):
    """Atomically delete one record without rewriting the whole entity."""
    entity = _validate_data_entity(entity)
    if not record_id or not expected_updated_at:
        raise DataConflictError("Не удалось проверить версию записи. Обновите страницу и повторите.")
    if saas_enabled() and not can("write_data"):
        raise PermissionError("У вашей роли нет прав на изменение данных магазина.")
    try:
        _rest_post("/rest/v1/rpc/save_app_data_batch", st.session_state.get("saas_access_token"), {
            "p_tenant_id": tenant_id(), "p_entity": entity,
            "p_rows": [{"record_id": str(record_id), "expected_updated_at": expected_updated_at,
                        "is_new": False, "is_deleted": True}],
        })
    except SupabaseRequestError as e:
        if e.status_code in (400, 409) and any(x in str(e) for x in ("DATA_CONFLICT", "RECORD_NOT_FOUND")):
            raise DataConflictError("Данные изменились в другой сессии. Обновите страницу и повторите.")
        raise


def data_save(entity,rows):
    entity = _validate_data_entity(entity)
    if not isinstance(rows, list):
        raise ValueError("Данные должны передаваться списком записей.")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Каждая запись данных должна быть объектом.")
    if saas_enabled() and not can("write_data"):
        raise PermissionError("У вашей роли нет прав на изменение данных магазина.")
    if saas_enabled() and entity=="settings" and not can("settings"):
        raise PermissionError("Только администратор или владелец может менять настройки магазина.")
    if not saas_enabled():
        os.makedirs("data",exist_ok=True)
        clean_rows=[_clean_payload(row) for row in rows]
        with open(f"data/{tenant_id()}_{entity}.json","w",encoding="utf-8") as f:
            json.dump(clean_rows,f,ensure_ascii=False,indent=2)
        return

    token=st.session_state.get("saas_access_token")
    session_key=_data_session_key(entity)
    baseline=dict(st.session_state.get(session_key, {}))

    current_ids=set()
    batch=[]
    for row in rows:
        clean=_clean_payload(row)
        record_id=str(row.get(_INTERNAL_RECORD_KEY) or _new_record_id())
        current_ids.add(record_id)
        if record_id in baseline:
            if clean != baseline[record_id].get("payload", {}):
                batch.append({
                    "record_id":record_id,
                    "payload":clean,
                    "expected_updated_at":baseline[record_id].get("updated_at"),
                    "is_new":False,
                    "is_deleted":False,
                })
        else:
            batch.append({
                "record_id":record_id,
                "payload":clean,
                "is_new":True,
                "is_deleted":False,
            })

    removed_ids=set(baseline)-current_ids
    for record_id in removed_ids:
        batch.append({
            "record_id":record_id,
            "expected_updated_at":baseline[record_id].get("updated_at"),
            "is_new":False,
            "is_deleted":True,
        })

    if not batch:
        return

    if any(
        not item.get("is_new") and not item.get("expected_updated_at")
        for item in batch
    ):
        raise DataConflictError(
            "Не удалось проверить версию одной из записей. Обновите данные и повторите сохранение."
        )

    # One database transaction now covers all changed/new/deleted rows.
    # Sort by stable record ID so concurrent sessions acquire row locks in a
    # deterministic order and are less likely to deadlock.
    batch.sort(key=lambda item: str(item["record_id"]))

    try:
        _rest_post(
            "/rest/v1/rpc/save_app_data_batch",
            token,
            {
                "p_tenant_id":tenant_id(),
                "p_entity":entity,
                "p_rows":batch,
            },
        )
    except SupabaseRequestError as e:
        message=str(e)
        if e.status_code in (400,409) and any(
            marker in message for marker in (
                "DATA_CONFLICT",
                "RECORD_NOT_FOUND",
                "RECORD_ALREADY_EXISTS",
            )
        ):
            raise DataConflictError(
                "Данные изменились в другой сессии. Обновите данные перед сохранением, чтобы не затереть чужие изменения."
            ) from e
        raise

    # Refresh the actual database versions only after the whole transaction
    # succeeds. If any row conflicts, PostgreSQL rolls the entire batch back.
    data_load(entity,[])

def _service_key():
    return _cfg("SUPABASE_SERVICE_ROLE_KEY")

# ==================== PLATFORM OWNER CONSOLE ====================
def platform_admin_enabled():
    email = _cfg("SAAS_ADMIN_EMAIL").lower()
    return bool(email and st.session_state.get("saas_email","").lower() == email)

def _admin_headers():
    key = _service_key()
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY не настроен.")
    return {"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json","Prefer":"return=representation"}

def _admin_request(method, path, **kwargs):
    url, _ = _supabase_config()
    if not url:
        raise RuntimeError("SUPABASE_URL не настроен.")
    headers = _admin_headers()
    r = requests.request(method, f"{url}{path}", headers=headers, timeout=20, **kwargs)
    try: data = r.json()
    except Exception: data = {"message": r.text}
    if not r.ok:
        raise RuntimeError(data.get("message") or data.get("error") or data.get("msg") or str(data))
    return data

def platform_admin_snapshot():
    if not platform_admin_enabled():
        raise PermissionError("Доступ к Owner Console запрещён.")
    tenants = _admin_request("GET","/rest/v1/tenants",params={"select":"id,name,slug,plan,status,created_at","order":"created_at.desc"})
    subs = _admin_request("GET","/rest/v1/subscriptions",params={"select":"tenant_id,plan,status,provider,current_period_end,auto_renew","order":"tenant_id"})
    members = _admin_request("GET","/rest/v1/memberships",params={"select":"tenant_id,user_id,role,created_at","order":"created_at.asc"})
    try:
        users = _admin_request("GET","/auth/v1/admin/users",params={"page":1,"per_page":1000}).get("users",[])
    except Exception:
        users = []
    return tenants, subs, members, users

def platform_admin_set_tenant(tenant_id_value, plan=None, status=None):
    if not platform_admin_enabled():
        raise PermissionError("Доступ к Owner Console запрещён.")
    payload={}
    if plan is not None: payload["plan"]=plan
    if status is not None: payload["status"]=status
    if not payload: return
    _admin_request("PATCH",f"/rest/v1/tenants",params={"id":f"eq.{tenant_id_value}"},json=payload)

def platform_admin_set_subscription(tenant_id_value, plan=None, status=None, auto_renew=None):
    if not platform_admin_enabled():
        raise PermissionError("Доступ к Owner Console запрещён.")
    payload={}
    if plan is not None: payload["plan"]=plan
    if status is not None: payload["status"]=status
    if auto_renew is not None: payload["auto_renew"]=bool(auto_renew)
    if not payload: return
    _admin_request("PATCH",f"/rest/v1/subscriptions",params={"tenant_id":f"eq.{tenant_id_value}"},json=payload)
