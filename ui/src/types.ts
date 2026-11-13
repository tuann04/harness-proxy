export interface ModelInfo {
  id: string
  label: string
  harness: string
}

export interface HarnessInfo {
  id: string
  name: string
  binary: string | null
  available: boolean
  default_model: string
  models: ModelInfo[]
  detect_error: string | null
}

export interface ProxyConfig {
  active_harness: string
  active_model: string
  override_client_model: boolean
  timeout_seconds: number
  max_concurrency: number
}

export interface RequestRecord {
  id: string
  started_at: number
  harness: string
  model: string
  requested_model: string
  stream: boolean
  status: "running" | "ok" | "error"
  latency_ms: number | null
  input_chars: number
  output_chars: number
  usage: Record<string, number>
  prompt_preview: string
  response_preview: string
  error: string | null
}

export interface MonitorSnapshot {
  metrics: {
    total_requests: number
    active_count: number
    success_count: number
    error_count: number
    avg_latency_ms: number
  }
  active: RequestRecord[]
  history: RequestRecord[]
}

export interface StatusResponse {
  config: ProxyConfig
  resolved: {
    harness: string
    model: string
  }
  harnesses: Record<string, HarnessInfo>
  monitor: MonitorSnapshot
}

export interface TestResponse {
  ok: boolean
  harness: string
  model: string
  output?: string
  error?: string
  latency_ms: number
  usage?: Record<string, number>
}
