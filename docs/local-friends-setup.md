# Local friends / collaboration setup
#
# Friends live in Supabase (`friendships`). Without
# `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, `/api/friends*` returns
# `CLOUD_STORAGE_REQUIRED` and the Friends UI shows a setup notice.

## 1. Create `.env`

```powershell
Copy-Item .env.example .env
```

## 2. Fill Supabase credentials

In the [Supabase dashboard](https://supabase.com/dashboard) → your project → **Project Settings → API**:

| Env | Where |
|-----|--------|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role` secret (server only; never expose to the browser) |

Optional: `SUPABASE_ANON_KEY` — not used for friends writes.

## 3. Apply SQL schemas (SQL Editor)

Run in order if the project is new:

1. `docs/supabase_tenant_schema.sql` — users + projects
2. `docs/supabase_knowledge_cards_schema.sql` — knowledge cards (if using cards)
3. `docs/supabase_social_collab_schema.sql` — `display_name`, `friendships`, `project_members`

All scripts are idempotent (`if not exists` / `add column if not exists`).

## 4. Restart the web server

```powershell
# stop the process on port 8000, then:
.\.venv\Scripts\Activate.ps1
math-agent-web
```

Confirm:

```powershell
curl.exe -s http://127.0.0.1:8000/api/auth/config
# expect: "cloud_storage_configured": true
```

## 5. Phone auth (to add friends by phone)

Friends lookup uses registered phone numbers in `conjecta_users`. Local
unauthenticated mode uses a sentinel user and cannot meaningfully friend others.

1. Set `CONJECTA_JWT_SECRET` (≥ 32 bytes) in `.env`.
2. Configure Aliyun Dypns (`ALIYUN_*`) **or** set `CONJECTA_SMS_DEBUG=1` with
   Dypns keys so verify responses can include the code in debug mode.
3. Restart, open `/app`, log in with two phones, then use **好友 → 添加好友**.

> Tip: leave JWT/SMS commented while you only need local solving; enable them
> when you are ready to test the social graph.

## Verify friends API

```powershell
# After login (cookie session):
curl.exe -s -b cookies.txt http://127.0.0.1:8000/api/friends
# expect: {"ok":true,"friends":[...]}  not CLOUD_STORAGE_REQUIRED
```
