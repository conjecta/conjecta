# Operations dashboard

The administrator dashboard is served at `/admin`. Access is enforced by the
backend; hiding the frontend entry is not the security boundary.

## Enable it

1. Apply `docs/supabase_operations_schema.sql` to the same Supabase project used
   by Conjecta.
2. Apply `docs/supabase_solve_feedback_schema.sql` to enable solve feedback
   collection and the **Feedback** tab.
3. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the backend.
4. Set `CONJECTA_ADMIN_PHONES` to a comma-separated list of mainland China
   mobile numbers. When omitted, **no phone is admin** (empty default — set
   this explicitly in production).
5. Rebuild the frontend and restart the backend.

The dashboard shows masked phone numbers only. It records solve prompts for the
administrator's run-history view, plus provider-reported input, output, cached,
reasoning, and total token counts. Provider-reported values are authoritative;
calls made through a relay that does not return usage metadata remain visible as
runs but have zero recorded tokens. The **Feedback** tab lists solve ratings,
outcomes, problem previews, and optional comments for administrators.
