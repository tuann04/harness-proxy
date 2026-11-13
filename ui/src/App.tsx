import * as React from "react"
import { Button } from "@ui-sdk/react/button"
import {
  Dialog,
  DialogBackdrop,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@ui-sdk/react/dialog"
import { Field, FieldDescription, FieldLabel } from "@ui-sdk/react/field"
import { Input } from "@ui-sdk/react/input"
import {
  NumberInput,
  NumberInputControl,
  NumberInputDecrement,
  NumberInputGroup,
  NumberInputIncrement,
} from "@ui-sdk/react/number-input"
import {
  SegmentedControl,
  SegmentedControlIndicator,
  SegmentedControlItem,
} from "@ui-sdk/react/segmented-control"
import {
  Select,
  SelectContent,
  SelectEmpty,
  SelectInput,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectList,
  SelectTrigger,
  SelectValue,
} from "@ui-sdk/react/select"
import { Switch, SwitchThumb } from "@ui-sdk/react/switch"
import { Tabs, TabsList, TabsPanel, TabsTab } from "@ui-sdk/react/tabs"
import { Textarea } from "@ui-sdk/react/textarea"
import {
  Toast,
  ToastDescription,
  ToastRegion,
  ToastTitle,
  createToaster,
} from "@ui-sdk/react/toast"
import {
  ActivityIcon,
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  MinusIcon,
  MoonIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  SaveIcon,
  SunIcon,
  TerminalIcon,
  XIcon,
} from "lucide-react"
import type { RequestRecord, StatusResponse, TestResponse } from "./types"

const HARNESS_ORDER = ["opencode", "agy", "claude"] as const

interface ModelOption {
  value: string
  label: string
}

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString()
}

export function App() {
  const [status, setStatus] = React.useState<StatusResponse | null>(null)
  const [sseState, setSseState] = React.useState<"connecting" | "live" | "reconnecting">("connecting")
  const [colorScheme, setColorScheme] = React.useState<"dark" | "light">("dark")
  const [activeTab, setActiveTab] = React.useState("overview")

  // Form state
  const [harness, setHarness] = React.useState("auto")
  const [model, setModel] = React.useState("")
  const [customModel, setCustomModel] = React.useState("")
  const [overrideModel, setOverrideModel] = React.useState(true)
  const [timeoutSec, setTimeoutSec] = React.useState<number | null>(180)
  const [maxConcurrency, setMaxConcurrency] = React.useState<number | null>(2)
  const [saving, setSaving] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)

  // Playground state
  const [prompt, setPrompt] = React.useState("Reply with only: OK")
  const [testing, setTesting] = React.useState(false)
  const [testResult, setTestResult] = React.useState<TestResponse | null>(null)

  // Request monitor state
  const [reqFilter, setReqFilter] = React.useState<"all" | "running" | "ok" | "error">("all")
  const [selectedReq, setSelectedReq] = React.useState<RequestRecord | null>(null)

  const [toaster] = React.useState(() => createToaster())
  const formDirtyRef = React.useRef(false)

  React.useEffect(() => () => toaster.dismiss(), [toaster])

  const syncForm = React.useCallback((data: StatusResponse) => {
    setHarness(data.config.active_harness)
    setModel(data.config.active_model)
    setOverrideModel(data.config.override_client_model)
    setTimeoutSec(data.config.timeout_seconds)
    setMaxConcurrency(data.config.max_concurrency)

    const targetHarness =
      data.config.active_harness === "auto" ? data.resolved.harness : data.config.active_harness
    const knownModels = data.harnesses[targetHarness]?.models ?? []
    const isKnown =
      !data.config.active_model || knownModels.some((m) => m.id === data.config.active_model)
    setCustomModel(isKnown ? "" : data.config.active_model)
    formDirtyRef.current = false
  }, [])

  const fetchStatus = React.useCallback(
    async (preserveForm: boolean) => {
      const res = await fetch("/api/status")
      const data: StatusResponse = await res.json()
      setStatus(data)
      if (!preserveForm || !formDirtyRef.current) {
        syncForm(data)
      }
    },
    [syncForm]
  )

  React.useEffect(() => {
    fetchStatus(false)
    const es = new EventSource("/api/events")
    es.onopen = () => setSseState("live")
    es.onmessage = () => fetchStatus(true)
    es.onerror = () => setSseState("reconnecting")
    return () => es.close()
  }, [fetchStatus])

  const modelOptions = React.useMemo<ModelOption[]>(() => {
    if (!status) return [{ value: "", label: "Harness default" }]
    const targetId = harness === "auto" ? status.resolved.harness : harness
    const list = status.harnesses[targetId]?.models ?? []
    return [
      { value: "", label: "Harness default" },
      ...list.map((m) => ({
        value: m.id,
        label: m.label && m.label !== m.id ? `${m.id} (${m.label})` : m.id,
      })),
    ]
  }, [status, harness])

  async function saveConfig(overridePayload?: Partial<StatusResponse["config"]>) {
    setSaving(true)
    try {
      const payload = overridePayload ?? {
        active_harness: harness,
        active_model: customModel.trim() || model,
        override_client_model: overrideModel,
        timeout_seconds: timeoutSec ?? 180,
        max_concurrency: maxConcurrency ?? 2,
      }
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      const data: StatusResponse = await res.json()
      setStatus(data)
      syncForm(data)
      toaster("Configuration saved", {
        description: `Serving ${data.resolved.harness}:${data.resolved.model || "default"}`,
      })
    } catch (err) {
      toaster("Failed to save config", { description: String(err) })
    } finally {
      setSaving(false)
    }
  }

  async function handleRefresh() {
    setRefreshing(true)
    try {
      const res = await fetch("/api/refresh", { method: "POST" })
      const data: StatusResponse = await res.json()
      setStatus(data)
      toaster("Harnesses re-scanned")
    } finally {
      setRefreshing(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    try {
      const res = await fetch("/api/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      })
      const data: TestResponse = await res.json()
      setTestResult(data)
      toaster(data.ok ? "Test succeeded" : "Test failed", {
        description: `${data.harness}:${data.model} in ${data.latency_ms} ms`,
      })
    } catch (err) {
      setTestResult({
        ok: false,
        harness: status?.resolved.harness ?? "",
        model: status?.resolved.model ?? "",
        error: String(err),
        latency_ms: 0,
      })
    } finally {
      setTesting(false)
    }
  }

  function copyEndpoint() {
    const snippet = `OPENAI_BASE_URL=${window.location.origin}/v1\nOPENAI_API_KEY=harness-proxy`
    navigator.clipboard?.writeText(snippet)
    toaster("Copied to clipboard", { description: `${window.location.origin}/v1` })
  }

  const metrics = status?.monitor.metrics ?? {
    total_requests: 0,
    active_count: 0,
    success_count: 0,
    error_count: 0,
    avg_latency_ms: 0,
  }

  const allRows = React.useMemo(() => {
    if (!status) return []
    const combined = [...status.monitor.active, ...status.monitor.history]
    if (reqFilter === "all") return combined
    return combined.filter((r) => r.status === reqFilter)
  }, [status, reqFilter])

  const resolvedLabel = status?.resolved.harness
    ? `${status.resolved.harness}:${status.resolved.model || "default"}`
    : "none available"

  return (
    <div data-example="" data-color-scheme={colorScheme} className="app-root">
      <div className="app-container">
        <header className="topbar">
          <div className="brand">
            <div className="brand-mark">
              <TerminalIcon size={18} />
            </div>
            <div>
              <h1 className="brand-title">Harness Proxy</h1>
              <div className="brand-sub">
                OpenAI-compatible router for local agent CLIs
              </div>
            </div>
          </div>

          <div className="topbar-right">
            <span data-slot="badge" className={sseState === "live" ? "badge-ok" : "badge-warn"}>
              <span className={`status-dot ${sseState === "live" ? "ok" : "warn"}`} style={{ marginRight: 6 }} />
              {sseState === "live" ? "Live" : sseState === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
            <span data-slot="badge" className="mono">
              {resolvedLabel}
            </span>
            <SegmentedControl
              value={colorScheme}
              onValueChange={(next) => next && setColorScheme(next as "dark" | "light")}
              aria-label="Color theme"
            >
              <SegmentedControlIndicator />
              <SegmentedControlItem value="light" aria-label="Light theme">
                <SunIcon size={14} />
              </SegmentedControlItem>
              <SegmentedControlItem value="dark" aria-label="Dark theme">
                <MoonIcon size={14} />
              </SegmentedControlItem>
            </SegmentedControl>
          </div>
        </header>

        <section className="metrics-grid" aria-label="Proxy metrics">
          <div className="metric-card">
            <div className="metric-label">Total Requests</div>
            <div className="metric-value">{metrics.total_requests}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Active Now</div>
            <div className="metric-value">{metrics.active_count}</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Succeeded</div>
            <div className="metric-value" style={{ color: "#22c55e" }}>
              {metrics.success_count}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Errors</div>
            <div className="metric-value" style={{ color: "var(--danger)" }}>
              {metrics.error_count}
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Avg Latency</div>
            <div className="metric-value">{metrics.avg_latency_ms} ms</div>
          </div>
        </section>

        <Tabs value={activeTab} onValueChange={(next) => next && setActiveTab(next)}>
          <TabsList aria-label="Dashboard sections">
            <TabsTab value="overview">Overview & Config</TabsTab>
            <TabsTab value="playground">Playground</TabsTab>
            <TabsTab value="requests">
              Requests {allRows.length > 0 ? `(${allRows.length})` : ""}
            </TabsTab>
          </TabsList>

          <TabsPanel value="overview">
            <div className="two-col" style={{ marginTop: 16 }}>
              <div className="panel">
                <div className="panel-head">
                  <h2 className="panel-title">Detected Harnesses</h2>
                  <Button
                    type="button"
                    data-size="sm"
                    disabled={refreshing}
                    onClick={handleRefresh}
                  >
                    <RefreshCwIcon size={14} />
                    {refreshing ? "Scanning..." : "Re-scan"}
                  </Button>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {HARNESS_ORDER.map((hid) => {
                    const h = status?.harnesses[hid]
                    if (!h) return null
                    const isActive = status?.resolved.harness === hid
                    return (
                      <div key={hid} className="harness-card" data-active={isActive}>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <strong>{h.name}</strong>
                            {isActive && (
                              <span data-slot="badge" className="badge-ok">
                                Active
                              </span>
                            )}
                            {!h.available && (
                              <span data-slot="badge" className="badge-err">
                                Missing
                              </span>
                            )}
                          </div>
                          <div className="mono muted" style={{ marginTop: 4 }}>
                            {h.available
                              ? `${h.models.length} models · default: ${h.default_model}`
                              : "Binary not found in PATH"}
                          </div>
                          {h.detect_error && (
                            <div className="mono" style={{ color: "var(--danger)", marginTop: 4 }}>
                              {h.detect_error}
                            </div>
                          )}
                        </div>
                        {h.available && (
                          <Button
                            type="button"
                            data-size="sm"
                            data-variant={isActive ? "solid" : undefined}
                            onClick={() => saveConfig({ active_harness: hid, active_model: "" })}
                          >
                            {isActive ? "Selected" : "Use"}
                          </Button>
                        )}
                      </div>
                    )
                  })}
                </div>

                <div style={{ marginTop: 6 }}>
                  <div className="panel-head" style={{ marginBottom: 8 }}>
                    <span className="muted" style={{ fontSize: 12, fontWeight: 500 }}>
                      OpenAI-Compatible Client Config
                    </span>
                    <Button type="button" data-size="sm" onClick={copyEndpoint}>
                      <CopyIcon size={13} />
                      Copy
                    </Button>
                  </div>
                  <pre className="code-block">
                    {`OPENAI_BASE_URL=${window.location.origin}/v1\nOPENAI_API_KEY=harness-proxy`}
                  </pre>
                </div>
              </div>

              <div className="panel">
                <div className="panel-head">
                  <h2 className="panel-title">Routing & Runtime</h2>
                </div>

                <Field>
                  <FieldLabel>Active Harness</FieldLabel>
                  <SegmentedControl
                    value={harness}
                    onValueChange={(next) => {
                      if (!next) return
                      formDirtyRef.current = true
                      setHarness(next)
                      setModel("")
                      setCustomModel("")
                    }}
                  >
                    <SegmentedControlIndicator />
                    <SegmentedControlItem value="auto">Auto</SegmentedControlItem>
                    {HARNESS_ORDER.map((hid) => {
                      const avail = status?.harnesses[hid]?.available
                      return (
                        <SegmentedControlItem key={hid} value={hid} disabled={!avail}>
                          {hid}
                        </SegmentedControlItem>
                      )
                    })}
                  </SegmentedControl>
                </Field>

                <Field>
                  <FieldLabel>Active Model</FieldLabel>
                  <Select
                    items={modelOptions}
                    value={model}
                    onValueChange={(next) => {
                      formDirtyRef.current = true
                      setModel(next ?? "")
                      setCustomModel("")
                    }}
                    filter="contains"
                    placeholder="Select a model"
                  >
                    <SelectTrigger>
                      <SelectValue />
                      <ChevronDownIcon />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectInput aria-label="Search models" placeholder="Filter models..." />
                      <SelectEmpty>No matching models.</SelectEmpty>
                      <SelectList>
                        {(state) =>
                          (state.items as ModelOption[]).map((item) => (
                            <SelectItem key={item.value || "__default__"} value={item.value} label={item.label}>
                              <SelectItemText />
                              <SelectItemIndicator>
                                <CheckIcon />
                              </SelectItemIndicator>
                            </SelectItem>
                          ))
                        }
                      </SelectList>
                    </SelectContent>
                  </Select>
                </Field>

                <Field>
                  <FieldLabel>Custom Model Override (optional)</FieldLabel>
                  <Input
                    value={customModel}
                    onChange={(e) => {
                      formDirtyRef.current = true
                      setCustomModel(e.target.value)
                    }}
                    placeholder="Leave empty to use selected model above"
                  />
                </Field>

                <div className="form-grid-2">
                  <Field>
                    <NumberInput
                      value={timeoutSec}
                      min={10}
                      max={1800}
                      step={10}
                      onValueChange={(next) => {
                        formDirtyRef.current = true
                        setTimeoutSec(next)
                      }}
                    >
                      <FieldLabel>Timeout (sec)</FieldLabel>
                      <NumberInputGroup>
                        <NumberInputDecrement aria-label="Decrease timeout">
                          <MinusIcon />
                        </NumberInputDecrement>
                        <NumberInputControl />
                        <NumberInputIncrement aria-label="Increase timeout">
                          <PlusIcon />
                        </NumberInputIncrement>
                      </NumberInputGroup>
                    </NumberInput>
                  </Field>

                  <Field>
                    <NumberInput
                      value={maxConcurrency}
                      min={1}
                      max={16}
                      step={1}
                      onValueChange={(next) => {
                        formDirtyRef.current = true
                        setMaxConcurrency(next)
                      }}
                    >
                      <FieldLabel>Max Concurrency</FieldLabel>
                      <NumberInputGroup>
                        <NumberInputDecrement aria-label="Decrease concurrency">
                          <MinusIcon />
                        </NumberInputDecrement>
                        <NumberInputControl />
                        <NumberInputIncrement aria-label="Increase concurrency">
                          <PlusIcon />
                        </NumberInputIncrement>
                      </NumberInputGroup>
                    </NumberInput>
                  </Field>
                </div>

                <Field data-variant="card">
                  <div data-slot="stack">
                    <FieldLabel>Override client model</FieldLabel>
                    <FieldDescription>
                      Routes client model names (unless prefixed with harness:) to the active model above.
                    </FieldDescription>
                  </div>
                  <Switch
                    checked={overrideModel}
                    onCheckedChange={(next) => {
                      formDirtyRef.current = true
                      setOverrideModel(next)
                    }}
                  >
                    <SwitchThumb />
                  </Switch>
                </Field>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                  <Button
                    type="button"
                    data-variant="solid"
                    disabled={saving}
                    onClick={() => saveConfig()}
                  >
                    <SaveIcon size={14} />
                    {saving ? "Saving..." : "Save Config"}
                  </Button>
                </div>
              </div>
            </div>
          </TabsPanel>

          <TabsPanel value="playground">
            <div className="panel" style={{ marginTop: 16 }}>
              <div className="panel-head">
                <h2 className="panel-title">Quick Test</h2>
                <span className="mono muted">Target: {resolvedLabel}</span>
              </div>

              <Field>
                <FieldLabel>Prompt</FieldLabel>
                <Textarea
                  rows={4}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Enter a test prompt..."
                />
              </Field>

              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <Button
                  type="button"
                  data-variant="solid"
                  disabled={testing || !prompt.trim()}
                  onClick={handleTest}
                >
                  <PlayIcon size={14} />
                  {testing ? "Running..." : "Run Test Prompt"}
                </Button>
                {testResult && (
                  <span className="mono muted">
                    {testResult.ok ? "OK" : "Error"} via {testResult.harness}:{testResult.model} in{" "}
                    {testResult.latency_ms} ms
                  </span>
                )}
              </div>

              {testResult && (
                <pre className="code-block" style={{ color: testResult.ok ? "var(--ink)" : "var(--danger)" }}>
                  {testResult.ok ? testResult.output || "(empty output)" : testResult.error}
                </pre>
              )}
            </div>
          </TabsPanel>

          <TabsPanel value="requests">
            <div className="panel" style={{ marginTop: 16 }}>
              <div className="panel-head" style={{ flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <ActivityIcon size={16} />
                  <h2 className="panel-title">Live & Recent Requests</h2>
                </div>
                <SegmentedControl
                  value={reqFilter}
                  onValueChange={(next) => next && setReqFilter(next as typeof reqFilter)}
                >
                  <SegmentedControlIndicator />
                  <SegmentedControlItem value="all">All</SegmentedControlItem>
                  <SegmentedControlItem value="running">Running</SegmentedControlItem>
                  <SegmentedControlItem value="ok">OK</SegmentedControlItem>
                  <SegmentedControlItem value="error">Error</SegmentedControlItem>
                </SegmentedControl>
              </div>

              <div className="req-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Status</th>
                      <th>Target</th>
                      <th>Client Model</th>
                      <th>Latency</th>
                      <th>Chars (In/Out)</th>
                      <th>Preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allRows.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="muted">
                          No requests recorded yet.
                        </td>
                      </tr>
                    ) : (
                      allRows.map((r) => (
                        <tr key={r.id} className="req-row" onClick={() => setSelectedReq(r)}>
                          <td className="mono">{formatTime(r.started_at)}</td>
                          <td>
                            <span
                              data-slot="badge"
                              className={
                                r.status === "ok"
                                  ? "badge-ok"
                                  : r.status === "error"
                                    ? "badge-err"
                                    : "badge-warn"
                              }
                            >
                              {r.status}
                            </span>
                          </td>
                          <td className="mono">
                            {r.harness}:{r.model || "default"}
                          </td>
                          <td className="mono muted">{r.requested_model}</td>
                          <td className="mono">{r.latency_ms != null ? `${r.latency_ms} ms` : "..."}</td>
                          <td className="mono">
                            {r.input_chars} / {r.output_chars}
                          </td>
                          <td className="mono muted" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {r.error ? r.error : r.response_preview || r.prompt_preview}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </TabsPanel>
        </Tabs>

        <Dialog open={selectedReq !== null} onOpenChange={(open) => !open && setSelectedReq(null)}>
          <DialogBackdrop />
          <DialogContent className="dialog-wide">
            {selectedReq && (
              <>
                <div data-slot="stack">
                  <DialogTitle>
                    Request {selectedReq.id} ({selectedReq.status})
                  </DialogTitle>
                  <DialogDescription className="mono">
                    {selectedReq.harness}:{selectedReq.model || "default"} · client={selectedReq.requested_model} ·{" "}
                    {selectedReq.stream ? "stream" : "non-stream"} ·{" "}
                    {selectedReq.latency_ms != null ? `${selectedReq.latency_ms} ms` : "running"}
                  </DialogDescription>
                </div>

                <Field>
                  <FieldLabel>Prompt Preview ({selectedReq.input_chars} chars)</FieldLabel>
                  <pre className="code-block">{selectedReq.prompt_preview || "(empty)"}</pre>
                </Field>

                {selectedReq.error ? (
                  <Field>
                    <FieldLabel>Error</FieldLabel>
                    <pre className="code-block" style={{ color: "var(--danger)" }}>
                      {selectedReq.error}
                    </pre>
                  </Field>
                ) : (
                  <Field>
                    <FieldLabel>Response Preview ({selectedReq.output_chars} chars)</FieldLabel>
                    <pre className="code-block">{selectedReq.response_preview || "(empty)"}</pre>
                  </Field>
                )}

                {Object.keys(selectedReq.usage || {}).length > 0 && (
                  <Field>
                    <FieldLabel>Usage</FieldLabel>
                    <pre className="code-block">{JSON.stringify(selectedReq.usage, null, 2)}</pre>
                  </Field>
                )}

                <div data-slot="row" style={{ justifyContent: "flex-end" }}>
                  <DialogClose data-variant="solid">Close</DialogClose>
                </div>
                <DialogClose aria-label="Close">
                  <XIcon aria-hidden="true" />
                </DialogClose>
              </>
            )}
          </DialogContent>
        </Dialog>

        <ToastRegion toaster={toaster}>
          {() => (
            <Toast>
              <div data-slot="stack">
                <ToastTitle />
                <ToastDescription />
              </div>
            </Toast>
          )}
        </ToastRegion>
      </div>
    </div>
  )
}
