import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../App.jsx";
import { Alert, Field, Masthead, PageFade, Spinner, Toggle } from "../components/ui.jsx";

function Section({ title, note, children }) {
  return (
    <div className="panel">
      <div className="panel-title">{title}</div>
      {note && <p className="panel-note">{note}</p>}
      {children}
    </div>
  );
}

function SaveBtn({ onClick, busy, children = "Save" }) {
  return (
    <button className="btn accent" onClick={onClick} disabled={busy}>
      {busy ? <Spinner /> : children}
    </button>
  );
}

export default function Settings() {
  const { refresh } = useApp();
  const [data, setData] = useState(null);
  const [cfg, setCfg] = useState({});
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState("");
  const fileRef = useRef();

  async function load() {
    const d = await api.get("/settings");
    setData(d); setCfg(d.config);
  }
  useEffect(() => { load().catch((e) => setError(e.message)); }, []);

  function set(k, v) { setCfg((c) => ({ ...c, [k]: v })); }

  async function run(key, fn, msg) {
    setBusy(key); setError(""); setFlash("");
    try { await fn(); if (msg) setFlash(msg); await load(); refresh(); }
    catch (e) { setError(e.message); }
    setBusy("");
  }

  const saveConfig = (keys, msg) =>
    run(msg, () => api.post("/settings/config", { updates: Object.fromEntries(keys.map((k) => [k, cfg[k]])) }), msg);

  if (!data) return <PageFade><div className="empty"><Spinner dark /> Loading settings…</div></PageFade>;

  // -------- LLM step form state
  return (
    <PageFade>
      <Masthead index="04 / Configure" eyebrow="App configuration" title="Settings">
        Upload your policy, pick models, and set routing thresholds. Company data stays in the files you provide.
      </Masthead>

      {flash && <Alert kind="ok">{flash}</Alert>}
      {error && <Alert kind="err">{error}</Alert>}

      <Section title="Policy document" note={`Loaded: ${data.policy.filename} · ${data.policy.rules} indexed rules · categories: ${data.policy.categories.join(", ") || "none"}`}>
        {data.policy.preview && <div className="email-block" style={{ marginBottom: 14 }}>{data.policy.preview.slice(0, 600)}</div>}
        <input ref={fileRef} type="file" accept=".pdf,.docx,.md,.txt" style={{ marginBottom: 12 }} />
        <div>
          <SaveBtn busy={busy === "policy"} onClick={() => {
            const f = fileRef.current?.files?.[0];
            if (!f) { setError("Choose a file first."); return; }
            const form = new FormData(); form.append("file", f);
            run("policy", () => api.postForm("/settings/policy", form), "Policy replaced and re-indexed.");
          }}>Replace policy & re-index</SaveBtn>
        </div>
      </Section>

      <ExampleReplies data={data} onSave={(rows) => run("examples", () => api.post("/settings/examples", { examples: rows }), "Examples saved.")} busy={busy === "examples"} />

      <Section title="Policy retrieval" note="How policy rules are found for each email. Cross-encoder rerank is optional and slower.">
        <label className="field"><Toggle checked={!!cfg.use_embeddings} onChange={(v) => set("use_embeddings", v)} label="Use local embeddings" /></label>
        <div className="row">
          <Field label="RRF k"><input type="number" value={cfg.rrf_k} onChange={(e) => set("rrf_k", Number(e.target.value))} style={{ width: 120 }} /></Field>
          <Field label="Top-k policy rules"><input type="number" value={cfg.k_policy} onChange={(e) => set("k_policy", Number(e.target.value))} style={{ width: 120 }} /></Field>
        </div>
        <label className="field"><Toggle checked={!!cfg.cross_encoder_rerank} onChange={(v) => set("cross_encoder_rerank", v)} label="Cross-encoder rerank (optional)" /></label>
        <label className="field"><Toggle checked={!!cfg.policy_llm_chunking} onChange={(v) => set("policy_llm_chunking", v)} label="LLM fallback for unstructured sections" /></label>
        <SaveBtn busy={busy === "retrieval"} onClick={() => saveConfig(["use_embeddings", "rrf_k", "k_policy", "cross_encoder_rerank", "policy_llm_chunking"], "retrieval")}>Save retrieval settings</SaveBtn>
      </Section>

      <LLMSection data={data} run={run} busy={busy} />

      <Section title="Evaluation gates" note="Controls when a reply can go AUTO. Lower faithfulness blocks auto-send.">
        <Field label={`Faithfulness gate (block AUTO below): ${Number(cfg.faithfulness_gate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.05} value={cfg.faithfulness_gate} onChange={(e) => set("faithfulness_gate", Number(e.target.value))} />
        </Field>
        <Field label={`Disagreement check sample rate: ${Number(cfg.retrieval_disagreement_sample_rate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.05} value={cfg.retrieval_disagreement_sample_rate} onChange={(e) => set("retrieval_disagreement_sample_rate", Number(e.target.value))} />
        </Field>
        <Field label={`AUTO audit sample rate: ${Number(cfg.audit_sample_rate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.01} value={cfg.audit_sample_rate} onChange={(e) => set("audit_sample_rate", Number(e.target.value))} />
        </Field>
        <SaveBtn busy={busy === "gates"} onClick={() => saveConfig(["faithfulness_gate", "retrieval_disagreement_sample_rate", "audit_sample_rate"], "gates")}>Save evaluation gates</SaveBtn>
      </Section>

      <Section title="Email connection" note="demo is offline (empty inbox, dry-run sends). mcp connects to a live mailbox via an MCP server.">
        <Field label="Email source">
          <select value={cfg.email_source} onChange={(e) => set("email_source", e.target.value)}>
            <option value="demo">demo (offline)</option><option value="mcp">mcp</option>
          </select>
        </Field>
        <div className="btn-row">
          <SaveBtn busy={busy === "email"} onClick={() => saveConfig(["email_source"], "email")}>Save email source</SaveBtn>
          <button className="btn ghost" disabled={busy === "test-inbox"} onClick={() => run("test-inbox", async () => {
            const r = await api.post("/settings/test/inbox"); setFlash((r.ok ? "Inbox OK: " : "Inbox failed: ") + r.detail);
          })}>{busy === "test-inbox" ? <Spinner dark /> : "Test inbox connection"}</button>
        </div>
      </Section>

      <Section title="Automation thresholds" note="Confidence is 0 to 100. At or above T1 can go AUTO. Below T2 escalates. In between, it goes to review.">
        <Field label={`T1 (auto-reply at or above): ${Math.round(cfg.t1)}`}>
          <input type="range" min={0} max={100} value={cfg.t1} onChange={(e) => set("t1", Number(e.target.value))} />
        </Field>
        <Field label={`T2 (escalate below): ${Math.round(cfg.t2)}`}>
          <input type="range" min={0} max={100} value={cfg.t2} onChange={(e) => set("t2", Number(e.target.value))} />
        </Field>
        <label className="field"><Toggle checked={!!cfg.live_send} onChange={(v) => set("live_send", v)} label="Live send (actually dispatch auto-replies)" /></label>
        <SaveBtn busy={busy === "auto"} onClick={() => {
          if (cfg.t2 >= cfg.t1) { setError("T2 must be below T1."); return; }
          saveConfig(["t1", "t2", "live_send"], "auto");
        }}>Save automation settings</SaveBtn>
      </Section>

      <Section title="Notifications" note="Optionally email a digest of pending review items after each sync.">
        <label className="field"><Toggle checked={!!cfg.digest_enabled} onChange={(v) => set("digest_enabled", v)} label="Email a digest after each inbox sync" /></label>
        <Field label="Digest recipient email"><input type="email" value={cfg.digest_recipient || ""} onChange={(e) => set("digest_recipient", e.target.value)} /></Field>
        <div className="btn-row">
          <SaveBtn busy={busy === "notify"} onClick={() => saveConfig(["digest_enabled", "digest_recipient"], "notify")}>Save notifications</SaveBtn>
          <button className="btn ghost" disabled={busy === "digest"} onClick={() => run("digest", async () => {
            const d = await api.post("/notify/digest"); setFlash(d.detail);
          })}>{busy === "digest" ? <Spinner dark /> : "Send digest now"}</button>
        </div>
      </Section>

      <StorageSection cfg={cfg} set={set} saveConfig={saveConfig} run={run} busy={busy} setFlash={setFlash} />
    </PageFade>
  );
}

// ---------------------------------------------------------------- LLM by step
function LLMSection({ data, run, busy }) {
  const [step, setStep] = useState("generate");
  const cur = step === "generate"
    ? { provider: data.llm.gen_provider, model: data.llm.gen_model }
    : { provider: data.llm.cls_provider, model: data.llm.cls_model };
  const [provider, setProvider] = useState(cur.provider);
  const [model, setModel] = useState(cur.model);
  const [key, setKey] = useState("");
  useEffect(() => { setProvider(cur.provider); setModel(cur.model); setKey(""); }, [step]); // eslint-disable-line

  return (
    <Section title="LLM models" note="Generation drafts replies and scores them. Categorization sorts inbound mail. Leave the API key blank to keep the one you already saved.">
      <div className="tabs" style={{ marginBottom: 18 }}>
        <button className={"tab" + (step === "generate" ? " active" : "")} onClick={() => setStep("generate")}>Email generation</button>
        <button className={"tab" + (step === "classify" ? " active" : "")} onClick={() => setStep("classify")}>Email categorization</button>
      </div>
      <div className="row" style={{ gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        {data.providers.map((p) => (
          <span className="chip" key={p.name}>{p.name}: {p.configured ? "configured ✓" : "no key"}{p.used_for.length ? ` · ${p.used_for.join(", ")}` : ""}</span>
        ))}
      </div>
      <Field label="Provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {data.providers.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
      </Field>
      <Field label="Model (blank = provider default)"><input type="text" value={model} onChange={(e) => setModel(e.target.value)} placeholder={data.llm.default_models?.[provider] || ""} /></Field>
      <Field label="API key (blank keeps existing)"><input type="password" value={key} onChange={(e) => setKey(e.target.value)} /></Field>
      <div className="btn-row">
        <button className="btn accent" disabled={busy === "llm"} onClick={() => run("llm", () => api.post("/settings/llm", { step, provider, model, api_key: key }), `Saved. ${step} now uses ${provider}.`)}>
          {busy === "llm" ? <Spinner /> : "Save model"}
        </button>
        <button className="btn ghost" disabled={busy === "test-llm"} onClick={() => run("test-llm", async () => {
          const r = await api.post(`/settings/test/${step}`);
          if (!r.ok) throw new Error(r.detail);
        }, "Connection OK.")}>{busy === "test-llm" ? <Spinner dark /> : "Test connection"}</button>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------- example replies
function ExampleReplies({ data, onSave, busy }) {
  const [rows, setRows] = useState(data.examples || []);
  const [email, setEmail] = useState(""); const [reply, setReply] = useState(""); const [oid, setOid] = useState("");
  useEffect(() => { setRows(data.examples || []); }, [data.examples]);

  function add() {
    if (!email.trim() || !reply.trim()) return;
    const next = [...rows, { incoming_email: email.trim(), actual_reply: reply.trim(), ...(oid.trim() ? { order_id: oid.trim() } : {}) }];
    setRows(next); setEmail(""); setReply(""); setOid(""); onSave(next);
  }
  function del(i) { const next = rows.filter((_, j) => j !== i); setRows(next); onSave(next); }

  return (
    <Section title="Example replies" note="Past email and reply pairs used to match your writing style. They are not used in batch evaluation.">
      <Field label="Customer email"><textarea rows={3} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Agent reply sent"><textarea rows={3} value={reply} onChange={(e) => setReply(e.target.value)} /></Field>
      <div className="row">
        <Field label="Order ID (optional)"><input type="text" value={oid} onChange={(e) => setOid(e.target.value)} style={{ width: 180 }} /></Field>
        <button className="btn accent" style={{ alignSelf: "center" }} onClick={add} disabled={busy || !email.trim() || !reply.trim()}>{busy ? <Spinner /> : "Add example"}</button>
      </div>
      {rows.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          {rows.map((r, i) => (
            <div className="row" key={i} style={{ borderTop: "1px solid var(--line-2)", padding: "10px 0", alignItems: "flex-start" }}>
              <span className="mono" style={{ minWidth: 52 }}>{r.ticket_id || `#${i + 1}`}</span>
              <span className="grow muted" style={{ fontSize: ".82rem" }}>{(r.incoming_email || "").slice(0, 110)}</span>
              <button className="btn sm ghost" onClick={() => del(i)}>Delete</button>
            </div>
          ))}
        </div>
      ) : <p className="panel-note" style={{ marginTop: 12 }}>No user-supplied examples yet.</p>}
    </Section>
  );
}

// ---------------------------------------------------------------- storage
function StorageSection({ cfg, set, saveConfig, run, busy, setFlash }) {
  const [secrets, setSecrets] = useState({});
  const ss = (k, v) => setSecrets((s) => ({ ...s, [k]: v }));
  return (
    <Section title="Storage" note="Where queue, feedback, and scores are stored. Local needs no setup. Cloud options are optional; secrets stay in .env.">
      <div className="row">
        <Field label="Structured store">
          <select value={cfg.storage_structured_provider} onChange={(e) => set("storage_structured_provider", e.target.value)}>
            <option value="local">local</option><option value="postgres">postgres</option>
          </select>
        </Field>
        <Field label="Blob store">
          <select value={cfg.storage_blob_provider} onChange={(e) => set("storage_blob_provider", e.target.value)}>
            {["local", "s3", "azure", "gcs", "postgres"].map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </Field>
      </div>
      <Field label="S3 bucket"><input type="text" value={cfg.storage_s3_bucket || ""} onChange={(e) => set("storage_s3_bucket", e.target.value)} /></Field>
      <Field label="Postgres DSN (blank keeps existing)"><input type="password" onChange={(e) => ss("STORAGE_POSTGRES_DSN", e.target.value)} /></Field>
      <div className="btn-row">
        <button className="btn accent" disabled={busy === "storage"} onClick={() => run("storage", async () => {
          await api.post("/settings/config", { updates: {
            storage_structured_provider: cfg.storage_structured_provider,
            storage_blob_provider: cfg.storage_blob_provider,
            storage_s3_bucket: (cfg.storage_s3_bucket || "").trim(),
          } });
          const env = { STORAGE_STRUCTURED_PROVIDER: cfg.storage_structured_provider, STORAGE_BLOB_PROVIDER: cfg.storage_blob_provider };
          for (const [k, v] of Object.entries(secrets)) if (v && v.trim()) env[k] = v.trim();
          await api.post("/settings/env", { updates: env });
        }, "Storage settings saved.")}>{busy === "storage" ? <Spinner /> : "Save storage settings"}</button>
        <button className="btn ghost" disabled={busy === "test-storage"} onClick={() => run("test-storage", async () => {
          const r = await api.post("/settings/test/storage"); setFlash((r.ok ? "Storage OK: " : "Storage failed: ") + r.detail);
        })}>{busy === "test-storage" ? <Spinner dark /> : "Test storage connection"}</button>
      </div>
    </Section>
  );
}
