// 后端调用封装。token 存 localStorage —— 将来要更严可换 httpOnly cookie。
const BASE = process.env.NEXT_PUBLIC_API_BASE || "/api";
const TOKEN_KEY = "lfp_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
  /** 403：知道你是谁，但你没这个权限 —— 留在原地提示，不要跳登录 */
  get isForbidden() {
    return this.status === 403;
  }
}

/** 网络层就没通 —— 和"服务器说不行"是两回事，提示也该不一样 */
export class NetworkError extends Error {
  constructor(public path: string) {
    super(
      `连不上后端（${path}）。多半是这三种情况之一：
` +
      `· 后端没起 —— 在 backend 目录跑 uvicorn kernel.web.app:app --port 8000
` +
      `· 前端刚重启过 —— 刷新一下页面（Ctrl+F5）
` +
      `· 检索相关的还要 :8100 —— 在 langchain-legal-RAG 目录跑 uvicorn src.service:app --port 8100`
    );
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers || {}),
      },
    });
  } catch {
    // ★ fetch 只在**网络层**失败时抛异常（服务没起、连接被重置、页面正好在重启中）。
    //   浏览器给的原话是 "Failed to fetch" —— 对用户零信息量，
    //   他既不知道是哪个服务没起，也不知道该干什么。这里换成能照做的话。
    throw new NetworkError(BASE + path);
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // 401 = 不知道你是谁 → 回登录页；403 = 知道但没权限 → 留在原地
    if (res.status === 401 && typeof window !== "undefined") {
      clearToken();
      window.location.href = "/login";
    }
    throw new ApiError(res.status, body.code || "unknown", body.message || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const send = (method: string) => <T,>(path: string, body?: unknown) =>
  api<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

export const post = send("POST");
export const put = send("PUT");
export const patch = send("PATCH");

// ---------------------------------------------------------------- 类型
// Step 3 手写；后续可由 FastAPI 的 OpenAPI 自动生成，避免手工同步

export type User = {
  id: number; username: string; full_name: string;
  email: string; phone: string; is_active: boolean; is_superuser: boolean;
  /** ★ 执业资格：所里给不了执业证，那是司法行政机关发的 */
  license_no?: string;
  license_status?: string;
  license_valid_to?: string | null;
  license_firm?: string;
};

export type License = {
  user_id: number; full_name: string;
  license_no: string; status: string; status_label: string;
  licensed_on: string; valid_to: string; firm: string;
  expired: boolean; can_practise: boolean;
  /** 要不要去核实 —— 空表示没问题 */
  note: string;
};

export const LICENSE_LABEL: Record<string, string> = {
  practising: "执业中", intern: "实习",
  suspended: "停止执业", revoked: "已注销", "": "未登记",
};

export const fetchLicense = (userId: number) =>
  api<License>(`/identity/users/${userId}/license`);
export const setLicense = (userId: number, body: {
  license_no?: string; status?: string;
  licensed_on?: string | null; valid_to?: string | null; firm?: string;
}) => put<License>(`/identity/users/${userId}/license`, body);

export type OrgUnit = {
  id: number; parent_id: number | null; kind: string;
  name: string; code: string; sort_order: number; is_active: boolean;
};

/** 角色跟组织走：同一个人在团队是"负责人"，在管委会是"主任" */
export type Membership = {
  id: number; user_id: number; org_unit_id: number; org_role: string;
  is_primary: boolean; valid_from: string; valid_to: string | null;
};

/** 职级跟人走：在哪个团队都一样。每个开关都驱动真实业务规则 */
export type Position = {
  id: number; code: string; name: string; category: string; rank: number;
  is_partner: boolean; can_bill: boolean;
  can_lead_team: boolean; can_handle_case: boolean;
};

export type Role = {
  id: number; code: string; name: string; data_scope: string;
  description: string; is_active: boolean; permissions: string[];
};

export type RoleAssignment = {
  id: number; user_id: number; role_id: number;
  org_unit_id: number | null; valid_from: string; valid_to: string | null;
};

export type Appointment = {
  id: number; user_id: number; position_id: number;
  valid_from: string; valid_to: string | null; note: string;
};

export type Permission = { code: string; module: string };

export type Me = {
  user: User;
  position: Position | null;
  memberships: Membership[];
  org_ids: number[];
  /** 前端据此显示/隐藏按钮；后端仍会二次校验，这里只是体验 */
  permissions: string[];
  /** 权限点 -> 数据范围 */
  scopes: Record<string, string>;
};

export const SCOPE_LABEL: Record<string, string> = {
  self: "仅本人", dept: "本部门", dept_tree: "本部门及下级",
  custom: "指定组织", all: "全所",
};

export const KIND_LABEL: Record<string, string> = {
  firm: "所", committee: "权力机构", department: "部门", team: "团队",
};

// ---------------------------------------------------------------- 接口

export const login = (tenant_code: string, username: string, password: string) =>
  post<{ access_token: string }>("/identity/login", { tenant_code, username, password });

export const fetchMe = () => api<Me>("/identity/me");
export const fetchOrgUnits = () => api<OrgUnit[]>("/identity/org-units");
export const fetchUsers = () => api<User[]>("/identity/users");
export const fetchPositions = () => api<Position[]>("/identity/positions");
export const fetchRoles = () => api<Role[]>("/identity/roles");
export const fetchPermissions = () => api<Permission[]>("/identity/permissions");

export const fetchUserPositions = (at?: string) =>
  api<Record<string, Position | null>>(`/identity/user-positions${at ? "?at=" + at : ""}`);

export const fetchMembers = (orgId: number, opts: { at?: string; subtree?: boolean } = {}) => {
  const q = new URLSearchParams();
  if (opts.at) q.set("at", opts.at);
  if (opts.subtree) q.set("include_descendants", "true");
  const qs = q.toString();
  return api<Membership[]>(`/identity/org-units/${orgId}/members${qs ? "?" + qs : ""}`);
};

export const fetchUserRoles = (userId: number) =>
  api<RoleAssignment[]>(`/identity/users/${userId}/roles`);
export const fetchAppointments = (userId: number) =>
  api<Appointment[]>(`/identity/users/${userId}/appointments`);

export const setRolePermissions = (code: string, permissions: string[]) =>
  put<Role>(`/identity/roles/${code}/permissions`, { permissions });
export const assignRole = (user_id: number, role_code: string, org_unit_id?: number | null) =>
  post<RoleAssignment>("/identity/role-assignments", { user_id, role_code, org_unit_id });
export const revokeRole = (id: number) =>
  post<RoleAssignment>(`/identity/role-assignments/${id}/revoke`);
export const updatePosition = (code: string, patch: Partial<Position>) =>
  api<{ code: string; changed: string[] }>(`/identity/positions/${code}`, {
    method: "PATCH", body: JSON.stringify(patch),
  });
export const promote = (userId: number, position_code: string, note = "", valid_from?: string) =>
  post<Appointment>(`/identity/users/${userId}/appointments`, { position_code, note, valid_from });

// ---------------------------------------------------------------- crm

/** 主体：自然人或法人。全所共享，是利冲检查的基本单位 */
export type Party = {
  id: number; kind: string; name: string;
  id_no: string | null; aliases: string; note: string;
};

export type PartyRelation = {
  id: number; from_party_id: number; to_party_id: number;
  relation: string; note: string;
};

export type Client = {
  id: number; party_id: number; code: string; status: string; source: string;
  owner_id: number; org_unit_id: number | null;
  onboarded_on: string | null; note: string;
};

export type Contact = {
  id: number; client_id: number; name: string; title: string;
  phone: string; email: string; is_primary: boolean; note: string;
};

export type ClientDetail = Client & {
  party: Party;
  contacts: Contact[];
  /** 关联方 —— 利冲检查顺着这些往外查一层 */
  related_party_ids: number[];
};

export const RELATION_LABEL: Record<string, string> = {
  parent: "母公司", subsidiary: "子公司", shareholder: "股东",
  legal_rep: "法定代表人", affiliate: "关联方", spouse: "配偶", relative: "亲属",
};

export const CLIENT_STATUS_LABEL: Record<string, string> = {
  prospect: "潜在", active: "正式", inactive: "停止合作",
};

export const fetchClients = () => api<Client[]>("/crm/clients");
export const fetchClientDetail = (id: number) => api<ClientDetail>(`/crm/clients/${id}`);
export const fetchParties = (keyword = "") =>
  api<Party[]>(`/crm/parties${keyword ? "?keyword=" + encodeURIComponent(keyword) : ""}`);
export const fetchSimilarParties = (name: string) =>
  api<Party[]>(`/crm/parties/similar?name=${encodeURIComponent(name)}`);

export const createParty = (body: {
  name: string; kind?: string; id_no?: string; aliases?: string;
  note?: string; allow_similar?: boolean;
}) => post<Party>("/crm/parties", body);

export const createClient = (body: {
  /** 编号留空则后端自动生成 KH2026-0007 */
  party_id: number; code?: string; owner_id?: number | null;
  org_unit_id?: number | null; status?: string; source?: string; note?: string;
}) => post<Client>("/crm/clients", body);

export const addContact = (clientId: number, body: {
  name: string; title?: string; phone?: string; email?: string; is_primary?: boolean;
}) => post<Contact>(`/crm/clients/${clientId}/contacts`, body);

export const relateParties = (body: {
  from_party_id: number; to_party_id: number; relation: string; note?: string;
}) => post<PartyRelation>("/crm/relations", body);

// ---------------------------------------------------------------- case

export type Cause = {
  id: number; parent_id: number | null; code: string; name: string;
  category: string; workflow_template: string; sort_order: number; is_active: boolean;
};

export type CaseRow = {
  id: number; code: string; title: string;
  cause_id: number | null; client_id: number | null; status: string;
  lead_id: number; org_unit_id: number | null;
  court: string; external_no: string; amount: string | null;
  opened_on: string | null; closed_on: string | null; summary: string;
};

export type CaseParty = {
  id: number; party_id: number; role: string; is_ours: boolean; note: string;
};
export type CaseMember = {
  id: number; user_id: number; role: string; joined_on: string; left_on: string | null;
};
export type Matter = {
  id: number; name: string; kind: string; status: string;
  court: string; external_no: string;
  opened_on: string | null; closed_on: string | null; note: string;
};
export type StatusLog = {
  id: number; from_status: string; to_status: string;
  actor_id: number | null; note: string;
};

export type CaseDetail = CaseRow & {
  parties: CaseParty[];
  members: CaseMember[];
  matters: Matter[];
  history: StatusLog[];
  /** 当前状态下允许进入的下一步 —— 前端只显示这些按钮 */
  next_statuses: string[];
};

export const CASE_STATUS_LABEL: Record<string, string> = {
  draft: "草稿", conflict_check: "利冲审查中", pending: "待立案",
  active: "办理中", closing: "结案中", closed: "已结案",
  archived: "已归档", terminated: "已终止",
};

export const PARTY_ROLE_LABEL: Record<string, string> = {
  plaintiff: "原告/申请人", defendant: "被告/被申请人", third_party: "第三人",
  appellant: "上诉人", appellee: "被上诉人", other: "其他",
};

export const MATTER_KIND_LABEL: Record<string, string> = {
  first_instance: "一审", second_instance: "二审", retrial: "再审",
  enforcement: "执行", arbitration: "仲裁", preservation: "保全",
  nonlit: "非诉工作包", other: "其他",
};

export const MEMBER_ROLE_LABEL: Record<string, string> = {
  lead: "主办", member: "协办", assistant: "助理",
};

export const CASE_CATEGORY_LABEL: Record<string, string> = {
  civil: "民事", criminal: "刑事", admin: "行政",
  arbitration: "仲裁", nonlit: "非诉",
};

/** 不可逆动作 —— 需要额外的 case.close 权限 */
export const IRREVERSIBLE_STATUSES = ["closed", "archived", "terminated"];

export const fetchCases = (opts: {
  keyword?: string; status?: string;
  /** 看板口径：open / new / closed。和 case.metrics() 逐字一致 */
  f?: string;
  mine?: boolean; org_id?: number; since?: string; until?: string;
} = {}) => {
  const q = new URLSearchParams();
  if (opts.keyword) q.set("keyword", opts.keyword);
  if (opts.status) q.set("status", opts.status);
  if (opts.f) q.set("f", opts.f);
  if (opts.mine) q.set("mine", "1");
  if (opts.org_id) q.set("org_id", String(opts.org_id));
  if (opts.since) q.set("since", opts.since);
  if (opts.until) q.set("until", opts.until);
  const qs = q.toString();
  return api<CaseRow[]>(`/case/cases${qs ? "?" + qs : ""}`);
};
/** 案件页每个页签上该显示几 —— 不点开就知道哪里有东西 */
export const fetchTabCounts = (caseId: number) =>
  api<Record<string, number>>(`/archive/cases/${caseId}/tab-counts`);

export const fetchCaseDetail = (id: number) => api<CaseDetail>(`/case/cases/${id}`);
export const transitionCase = (id: number, to_status: string, note = "") =>
  post<CaseRow>(`/case/cases/${id}/transition`, { to_status, note });

export const fetchCauses = () => api<Cause[]>("/case/causes");
export const createCause = (body: {
  code: string; name: string; category?: string;
  parent_id?: number | null; workflow_template?: string;
}) => post<Cause>("/case/causes", body);

// ---------------------------------------------------------------- retrieval

/** 一条可回溯的引用。source_id 是稳定标识，库重建后仍能定位 */
export type SearchCitation = {
  ordinal: number;
  source_type: string;
  /** ★ 法释〔2020〕25号#第十四条 / 指导案例7号 —— 不是 chunk_id */
  source_id: string;
  title: string;
  snippet: string;
  url: string;
  effective_status: string;
  /** 答复是否真的引用了它 */
  used: boolean;
};

export type SearchLog = {
  id: number;
  case_id: number | null;
  actor_id: number;
  kind: string;
  status: string;
  question: string;
  intent: string;
  answer: string;
  trace_id: string;
  corpus_version: string;
  tokens_in: number;
  tokens_out: number;
  error: string;
  created_at: string;
  finished_at: string | null;
};

export type SearchLogDetail = SearchLog & {
  citations: SearchCitation[];
  /** 检索服务改写出的检索式 —— 让律师看懂"它到底查了什么" */
  queries: string[];
  /** 引用校验告警：编造条号、查无此案、已废止 */
  warnings: string[];
  cost_cny: number;
};

export type RetrievalHealth = {
  status: string;
  corpus_version?: string;
  model?: string;
  embedding?: string;
  auth?: string;
  error?: string;
};

export const RETRIEVAL_KIND_LABEL: Record<string, string> = {
  search: "检索", answer: "问答", extract: "要素抽取", compare: "类案对比",
};

export const retrievalHealth = () => api<RetrievalHealth>("/retrieval/health");

/** 纯检索：0 成本、秒级。**没有「带本案背景」开关** —— 它不走改写节点 */
export const doSearch = (body: {
  query: string; case_id?: number | null; top_k?: number; scope?: string[];
}) => post<SearchLogDetail>("/retrieval/search", body);

/** 问答：有成本、任务化。返回 log，再轮询 */
export const askQuestion = (body: {
  kind?: string; question: string; case_id?: number | null;
  top_k?: number; with_context?: boolean;
}) => post<SearchLog>("/retrieval/ask", body);

export const pollLog = (logId: number) =>
  post<SearchLogDetail>(`/retrieval/logs/${logId}/poll`);
export const fetchLog = (logId: number) =>
  api<SearchLogDetail>(`/retrieval/logs/${logId}`);
export const fetchCaseLogs = (caseId: number) =>
  api<SearchLog[]>(`/retrieval/cases/${caseId}/logs`);
export const fetchRetrievalCost = () =>
  api<{ count: number; cost_cny: number; tokens_in: number; tokens_out: number }>(
    "/retrieval/cost");

// ---------------------------------------------------------------- 利冲（Step 7）

export type ConflictHit = {
  id: number;
  /** blocked 阻断 / review 需审查 / notice 提示 */
  level: string;
  /** direct / affiliate / firm_side / historical */
  kind: string;
  subject_party_id: number;
  matched_party_id: number | null;
  related_case_id: number | null;
  related_client_id: number | null;
  description: string;
  /** 逐条处置理由。空 = 还没处置 */
  resolution: string;
  resolved: boolean;
};

export type ConflictCheck = {
  id: number;
  case_id: number;
  /** cleared 通过 / pending 待管委会 / blocked 阻断 / waived 已放行 */
  status: string;
  requested_by: number | null;
  requested_at: string;
  decided_by: number | null;
  decided_at: string | null;
  decision_note: string;
};

export type ConflictCheckDetail = ConflictCheck & {
  hits: ConflictHit[];
  /** 检查当时的当事人快照 —— 事后要能还原"当时看到的是什么" */
  snapshot: string;
};

/** 只有这两个状态能让案件继续推进，和后端 PASSING_STATUSES 一致 */
export const CONFLICT_PASSING = ["cleared", "waived"];

export const CONFLICT_STATUS_LABEL: Record<string, string> = {
  cleared: "无冲突",
  pending: "待管委会决定",
  blocked: "已阻断",
  waived: "已放行",
};

export const HIT_LEVEL_LABEL: Record<string, string> = {
  blocked: "阻断",
  review: "需审查",
  notice: "提示",
};

export const HIT_KIND_LABEL: Record<string, string> = {
  direct: "对方是我方客户",
  affiliate: "对方是客户关联方",
  firm_side: "所内他人代理对方",
  historical: "已结案旧关系",
};

export const runConflictCheck = (caseId: number) =>
  post<ConflictCheckDetail>(`/conflict/cases/${caseId}/check`);
export const fetchLatestConflict = (caseId: number) =>
  api<ConflictCheckDetail | null>(`/conflict/cases/${caseId}/latest`);
export const fetchConflictChecks = (caseId: number) =>
  api<ConflictCheck[]>(`/conflict/cases/${caseId}/checks`);

/** 放行必须写理由；放行时可顺带设隔离墙 */
export const decideConflict = (checkId: number, body: {
  status: "waived" | "blocked";
  note: string;
  walls?: { user_id: number; mode?: string; reason?: string }[];
}) => post<ConflictCheckDetail>(`/conflict/checks/${checkId}/decide`, body);

export const resolveHit = (hitId: number, resolution: string) =>
  post<ConflictHit>(`/conflict/hits/${hitId}/resolve`, { resolution });

/** 隔离墙。★ 权限点是 identity.manage_wall，**不要求能看见该案件** ——
 *  否则把自己挡在外面之后就永远撤不掉了 */
export type Wall = {
  id: number;
  resource_type: string;
  resource_id: string;
  user_id: number;
  mode: string;
  reason: string;
  created_by: number | null;
};

export const fetchWalls = (resourceType: string, resourceId: string | number) =>
  api<Wall[]>(`/identity/walls?resource_type=${resourceType}&resource_id=${resourceId}`);

export const removeWall = (wallId: number) =>
  api<Wall>(`/identity/walls/${wallId}`, { method: "DELETE" });

// ---------------------------------------------------------------- 流程（Step 9/10）

export type WorkflowStepLog = {
  id: number;
  node: string;
  /** entered 进入 / asked 抛出确认 / answered 律师答复 / finished 完成 / failed 出错 */
  event: string;
  note: string;
  actor_id: number | null;
  created_at: string;
};

export type WorkflowRun = {
  run_id: string;
  case_id: number;
  template_key: string;
  /** running 跑着 / waiting_human 等律师 / done 完成 / failed 失败 / cancelled 已中止 */
  status: string;
  current_node: string;
  pending_question: string;
  error: string;
  started_by: number;
  started_at: string;
  finished_at: string | null;
};

/** 时限。★ fatal=true 是除斥期间 —— 过期即失权，不是"可能被抗辩" */
export type Deadline = {
  key: string;
  name: string;
  basis: string;
  fatal: boolean;
  note: string;
  /** expired 已过 / urgent 30天内 / unknown 算不出 / ok / info */
  status: string;
  anchor_on: string;
  deadline_on: string;
  days_left: number | null;
  message: string;
};

/** 按引用标识反查原文 —— 让办案流程里的每一条依据都点得开 */
export type LookupItem = {
  found: boolean;
  title?: string;
  text?: string;
  snippet?: string;
  effective_status?: string;
  locator?: string;
};
export type LookupOut = {
  items: Record<string, LookupItem>;
  corpus_version: string;
};
export const lookupSources = (source_ids: string[]) =>
  post<LookupOut>("/retrieval/lookup", { source_ids });

export type IssueCitation = {
  source_id: string;
  title: string;
  snippet: string;
  effective_status: string;
  query: string;
  ordinal?: number;
};

export type IssueResearch = {
  key: string;
  title: string;
  why: string;
  queries: string[];
  citations: IssueCitation[];
};

export type WorkflowState = {
  facts?: string;
  role?: string;
  elements?: Record<string, string | boolean>;
  deadlines?: Deadline[];
  research?: IssueResearch[];
  draft?: string;
  draft_error?: string;
  final?: string;
  cost_cny?: number;
  trail?: string[];
  research_failures?: string[];
};

export type WorkflowRunDetail = WorkflowRun & {
  steps: WorkflowStepLog[];
  state: WorkflowState;
};

export type WorkflowTemplate = {
  key: string;
  label: string;
  steps: { key: string; title: string }[];
};

export const WORKFLOW_STATUS_LABEL: Record<string, string> = {
  running: "推进中", waiting_human: "等你确认",
  done: "已完成", failed: "失败", cancelled: "已中止",
};

/** 要素字段。和后端 construction.ELEMENT_FIELDS 一一对应 */
export const ELEMENT_FIELDS: { key: string; label: string; bool?: boolean }[] = [
  { key: "project", label: "工程名称" },
  { key: "contract_amount", label: "合同价款" },
  { key: "unpaid_amount", label: "欠付金额" },
  { key: "completed_on", label: "竣工日期" },
  { key: "handover_on", label: "交付日期" },
  { key: "payable_on", label: "应付款日期" },
  { key: "settled_on", label: "结算日期" },
  { key: "used_without_acceptance", label: "未验收即使用", bool: true },
  { key: "borrowed_qualification", label: "借用资质", bool: true },
  { key: "no_qualification", label: "无相应资质", bool: true },
  { key: "subcontracted", label: "转包/违法分包", bool: true },
  { key: "multiple_contracts", label: "存在数份合同", bool: true },
  { key: "tendered", label: "经过招投标", bool: true },
  { key: "actual_builder", label: "我方为实际施工人", bool: true },
  { key: "delayed", label: "存在工期争议", bool: true },
];

export const fetchWorkflowTemplates = () =>
  api<WorkflowTemplate[]>("/workflow/templates");
export const fetchCaseRuns = (caseId: number) =>
  api<WorkflowRun[]>(`/workflow/cases/${caseId}/runs`);
export const fetchRun = (runId: string) =>
  api<WorkflowRunDetail>(`/workflow/runs/${runId}`);

export const startRun = (body: {
  template_key: string; case_id: number; payload?: Record<string, unknown>;
}) => post<WorkflowRunDetail>("/workflow/runs", body);

/** ★ data 是结构化修正：改抽错的日期、去掉不要的焦点。人说了算 */
export const resumeRun = (runId: string, answer: string, data?: Record<string, unknown>) =>
  post<WorkflowRunDetail>(`/workflow/runs/${runId}/resume`, { answer, data: data || {} });

export const cancelRun = (runId: string, note = "") =>
  post<WorkflowRun>(`/workflow/runs/${runId}/cancel`, { note });

// ---------------------------------------------------------------- 案卷文档（Step 11）

export type DocVersion = {
  id: number;
  version: number;
  filename: string;
  content_type: string;
  size: number;
  sha256: string;
  /** ok 能搜到 / empty 多半是扫描件 / unsupported 不解析 / failed 解析出错 */
  text_status: string;
  text_note: string;
  note: string;
  uploaded_by: number;
  uploaded_at: string;
};

export type DocRow = {
  id: number;
  case_id: number;
  folder: string;
  title: string;
  note: string;
  current_version: number;
  deleted: boolean;
  created_by: number;
  created_at: string;
  updated_at: string;
};

export type DocDetail = DocRow & { versions: DocVersion[] };

export type DocHit = {
  document_id: number;
  case_id: number;
  title: string;
  folder: string;
  version: number;
  version_id: number;
  filename: string;
  text_status: string;
  snippet: string;
};

export type Unsearchable = {
  document_id: number;
  title: string;
  folder: string;
  filename: string;
  text_status: string;
  reason: string;
};

export const TEXT_STATUS_LABEL: Record<string, string> = {
  ok: "可检索", empty: "无文字层", unsupported: "不解析", failed: "解析失败",
};

export const fetchFolders = () => api<string[]>("/doc/folders");
export const fetchDocs = (caseId: number, folder = "") =>
  api<DocRow[]>(`/doc/cases/${caseId}/documents${folder ? `?folder=${encodeURIComponent(folder)}` : ""}`);
export const fetchDoc = (id: number) => api<DocDetail>(`/doc/documents/${id}`);
export const fetchDocStats = (caseId: number) =>
  api<{ documents: number; bytes: number; unsearchable: number }>(`/doc/cases/${caseId}/stats`);
/** ★ 搜不到的文件清单 —— 律师搜不着时会以为「案卷里没有」 */
export const fetchUnsearchable = (caseId: number) =>
  api<Unsearchable[]>(`/doc/cases/${caseId}/unsearchable`);
export const searchDocs = (q: string, caseId?: number) =>
  api<DocHit[]>(`/doc/search?q=${encodeURIComponent(q)}${caseId ? `&case_id=${caseId}` : ""}`);
export const renameDoc = (id: number, body: { title?: string; folder?: string; note?: string }) =>
  api<DocRow>(`/doc/documents/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteDoc = (id: number) => post<DocRow>(`/doc/documents/${id}/delete`);
export const restoreDoc = (id: number) => post<DocRow>(`/doc/documents/${id}/restore`);

/** 上传走 multipart，不能带 Content-Type: application/json（边界符要浏览器自己填） */
export async function uploadDoc(form: {
  case_id: number; file: File; title?: string; folder?: string;
  note?: string; document_id?: number;
}): Promise<DocDetail> {
  const fd = new FormData();
  fd.append("case_id", String(form.case_id));
  fd.append("file", form.file);
  if (form.title) fd.append("title", form.title);
  if (form.folder) fd.append("folder", form.folder);
  if (form.note) fd.append("note", form.note);
  if (form.document_id) fd.append("document_id", String(form.document_id));

  const token = getToken();
  let res: Response;
  try {
    res = await fetch(BASE + "/doc/documents", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: fd,
    });
  } catch {
    throw new NetworkError(BASE + "/doc/documents");
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    if (res.status === 401 && typeof window !== "undefined") {
      clearToken();
      window.location.href = "/login";
    }
    throw new ApiError(res.status, b.code || "unknown", b.message || res.statusText);
  }
  return res.json();
}

/** 下载走后端转发（不是预签名链接）—— 每次下载都过权限、都留痕 */
export async function downloadVersion(versionId: number, filename: string) {
  const token = getToken();
  const res = await fetch(BASE + `/doc/versions/${versionId}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, "download", "下载失败");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------- 文书生成（Step 12）

export type DraftSection = {
  id: number;
  ordinal: number;
  key: string;
  title: string;
  /** fill 自动填充 / generate 模型起草 / manual 律师手写 */
  kind: string;
  kind_label: string;
  content: string;
  /** 这段实际怎么来的：fill / model / manual / empty */
  source: string;
  edited: boolean;
  /** 这一节引用到的依据编号 */
  used: number[];
  /** 引用校验的告警 —— 「这些是编的」就出现在这里 */
  warnings: string[];
  note: string;
  hint: string;
  required: boolean;
  updated_by: number | null;
  updated_at: string;
};

export type DraftRow = {
  id: number;
  case_id: number;
  template_key: string;
  title: string;
  /** draft 在改 / final 已定稿 / void 作废 */
  status: string;
  source_run_id: string;
  document_id: number | null;
  created_by: number;
  finalized_by: number | null;
  finalized_at: string | null;
  cost_cny: string;
  created_at: string;
  updated_at: string;
};

export type DraftCitation = {
  source_id: string;
  title: string;
  snippet: string;
  effective_status: string;
  issue: string;
};

export type DraftDetail = DraftRow & {
  sections: DraftSection[];
  citations: DraftCitation[];
  ready: boolean;
  blocking: string[];
  warnings: string[];
};

export type DraftTemplate = {
  key: string;
  label: string;
  whenever: string;
  sections: { key: string; title: string; kind: string; kind_label: string; required: boolean; hint: string }[];
};

export const DRAFT_STATUS_LABEL: Record<string, string> = {
  draft: "起草中", final: "已定稿", void: "已作废",
};
export const SOURCE_LABEL: Record<string, string> = {
  fill: "自动填充", model: "模型起草", manual: "律师手写", empty: "空",
};

export const fetchDraftTemplates = () => api<DraftTemplate[]>("/draft/templates");
export const fetchCaseDrafts = (caseId: number) =>
  api<DraftRow[]>(`/draft/cases/${caseId}/drafts`);
export const fetchDraft = (id: number) => api<DraftDetail>(`/draft/drafts/${id}`);
export const createDraft = (body: {
  case_id: number; template_key: string; title?: string;
  run_id?: string; queries?: string[];
}) => post<DraftDetail>("/draft/drafts", body);
export const generateSection = (id: number, key: string, body: { instruction?: string; force?: boolean } = {}) =>
  post<DraftDetail>(`/draft/drafts/${id}/sections/${key}/generate`, body);
export const editSection = (id: number, key: string, content: string) =>
  api<DraftDetail>(`/draft/drafts/${id}/sections/${key}`, {
    method: "PUT", body: JSON.stringify({ content }),
  });
export const draftText = (id: number) => api<{ text: string }>(`/draft/drafts/${id}/text`);
export const finalizeDraft = (id: number, note = "") =>
  post<DraftDetail>(`/draft/drafts/${id}/finalize`, { note });
export const voidDraft = (id: number) => post<DraftRow>(`/draft/drafts/${id}/void`);

// ---------------------------------------------------------------- 引用留痕（Step 13）

export type CiteSentence = {
  index: number;
  start: number;
  end: number;
  text: string;
  /** 去掉【S1】标记后的正文 */
  plain: string;
  /** ★ 原位片段：{t:"根据"} {cite:1} {t:"，发包人…"} ——
   *  剥掉标记再把编号补到句末，会读成「根据，建设工程未经…」 */
  segments: { t?: string; cite?: number }[];
  /** 这句话引了哪些依据（编号） */
  citations: number[];
  /** ★ 启发式提示：看着像法律论断但没标依据。**不是判定** */
  looks_legal: boolean;
};

export type CitePart = { key: string; title: string; sentences: CiteSentence[] };

export type CiteSource = {
  id: number;
  ordinal: number;
  source_type: string;
  source_id: string;
  title: string;
  snippet: string;
  effective_status: string;
  /** 语料版本指纹 —— 库重建后能区分这条引用出自哪一版 */
  corpus_version: string;
  /** 关联回当时那次检索 */
  trace_id: string;
  issue: string;
  retrieved_at: string;
};

export type CiteTrace = CiteSource & {
  locator: string;
  used_in: { part_key: string; sentence_index: number; sentence: string }[];
  /** 已失效/废止 —— 引它是事故 */
  stale: boolean;
};

export type CiteCoverage = {
  sentences: number;
  cited: number;
  /** 看着像论断却没依据的句数 */
  flagged: number;
  sources: number;
  /** 给了但一次没用上的依据编号 */
  unused: number[];
  /** 已失效的依据编号 */
  stale: number[];
};

export const fetchCiteMap = (kind: string, id: number) =>
  api<CitePart[]>(`/citation/${kind}/${id}/map`);
export const fetchCiteSources = (kind: string, id: number) =>
  api<CiteSource[]>(`/citation/${kind}/${id}/sources`);
export const fetchCiteCoverage = (kind: string, id: number) =>
  api<CiteCoverage>(`/citation/${kind}/${id}/coverage`);
export const fetchCiteTrace = (citationId: number) =>
  api<CiteTrace>(`/citation/citations/${citationId}`);

// ---------------------------------------------------------------- 建案（补 Step 6 漏掉的界面）

export const createCase = (body: {
  code: string; title: string; cause_id?: number | null; lead_id?: number | null;
  org_unit_id?: number | null; court?: string; external_no?: string;
  amount?: string | null; opened_on?: string | null; summary?: string;
}) => post<CaseDetail>("/case/cases", body);

/** 给案件挂当事人。★ party_id 指向全所主体库 —— 利冲靠它跨案件比对 */
export const addCaseParty = (caseId: number, body: {
  party_id: number; role: string; is_ours: boolean; note?: string;
}) => post<unknown>(`/case/cases/${caseId}/parties`, body);


// ---------------------------------------------------------------- 待办与时限（Step 14/15）

export type DeadlineItem = {
  id: number;
  case_id: number;
  rule_code: string;
  name: string;
  basis: string;
  /** ok / urgent / expired / unknown / no_fixed / done / waived */
  status: string;
  /** true = 除斥期间，过期即失权，**不许中断** */
  fatal: boolean;
  /** ★ 这条能不能登记中断。由后端判断 —— 除斥期间、没有固定期间、
   *  规则没标可中断，三种情形都是 false，界面别自己按 fatal 猜 */
  can_interrupt: boolean;
  anchor_field: string;
  anchor_label: string;
  anchor_on: string;
  due_on: string;
  days_left: number | null;
  message: string;
  /** ★ 这条规则盖不住什么 —— 提示不是承诺 */
  caveat: string;
  note: string;
  /** 中断之日（195 条）—— 从这天**重新起算**整个期间 */
  interrupted_on: string;
  interrupt_note: string;
  /** 中止事由**消除**之日（194 条）—— 自该日起满六个月届满。
   *  注意存的是消除日不是发生日，194 条的算式只用得上消除日 */
  suspend_end_on: string;
  suspend_note: string;
  /** 最长保护期届满日（188 条 2 款的 20 年）。空 = 这条没有上限 */
  outer_due_on: string;
  /** ★ 已经顶到最长保护期 —— **再中断也不会往后延**。
   *  届满日和上限重合时界面上只是个普通日期，含义却完全不同 */
  capped: boolean;
  /** 律师确认过没有。没确认的只能当提醒看 */
  confirmed: boolean;
  source: string;
};

export type TaskRow = {
  id: number;
  case_id: number | null;
  title: string;
  detail: string;
  assignee_id: number | null;
  due_on: string | null;
  priority: string;
  status: string;
  deadline_id: number | null;
  created_by: number;
  done_at: string | null;
  created_at: string;
};

export type DeadlineRule = {
  id: number; code: string; name: string; basis: string;
  cause_code: string; role: string; anchor_field: string;
  months: number; days: number; fatal: boolean; suspendable: boolean;
  outer_years: number;
  warn_days: number; caveat: string; note: string;
  builtin: boolean; active: boolean;
};

export type Board = {
  tasks: TaskRow[];
  deadlines: DeadlineItem[];
  expired: number; urgent: number; unknown: number; overdue_tasks: number;
};

export const DEADLINE_STATUS_LABEL: Record<string, string> = {
  ok: "正常", urgent: "临近", expired: "已过期",
  // ★ 这两个必须分开说。unknown = 补齐起算日就能算，要催；
  //    no_fixed = 规则本来就没有期间，催也没用，别占着红色计数
  unknown: "算不出", no_fixed: "无固定期间",
  done: "已行使", waived: "不适用",
};
export const TASK_STATUS_LABEL: Record<string, string> = {
  todo: "待办", doing: "进行中", done: "已完成", cancelled: "已取消",
};
export const PRIORITY_LABEL: Record<string, string> = {
  high: "高", normal: "普通", low: "低",
};

export const fetchDeadlines = (caseId: number) =>
  api<DeadlineItem[]>(`/task/cases/${caseId}/deadlines`);
export const generateDeadlines = (caseId: number, role = "") =>
  post<DeadlineItem[]>(`/task/cases/${caseId}/deadlines/generate?role=${role}`);
export const setAnchor = (id: number, anchor_on: string | null, note = "") =>
  post<DeadlineItem>(`/task/deadlines/${id}/anchor`, { anchor_on, note });
export const interruptDeadline = (id: number, on: string, note: string) =>
  post<DeadlineItem>(`/task/deadlines/${id}/interrupt`, { on, note });
/** 中止（194 条）。end_on 是事由**消除**之日，不是发生之日 */
export const suspendDeadline = (id: number, end_on: string, note: string) =>
  post<DeadlineItem>(`/task/deadlines/${id}/suspend`, { end_on, note });
export const markDeadline = (id: number, status: string, note = "") =>
  post<DeadlineItem>(`/task/deadlines/${id}/mark`, { status, note });
export const addDeadline = (body: {
  case_id: number; name: string; basis: string; due_on: string;
  fatal?: boolean; note?: string;
}) => post<DeadlineItem>("/task/deadlines", body);

export const fetchCaseTasks = (caseId: number) =>
  api<TaskRow[]>(`/task/cases/${caseId}/tasks`);
export const createTask = (body: {
  title: string; case_id?: number | null; detail?: string;
  assignee_id?: number | null; due_on?: string | null;
  priority?: string; deadline_id?: number | null;
}) => post<TaskRow>("/task/tasks", body);
export const updateTask = (id: number, body: Record<string, unknown>) =>
  api<TaskRow>(`/task/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const fetchBoard = () => api<Board>("/task/board");
export const fetchDeadlineRules = (causeCode = "") =>
  api<DeadlineRule[]>(`/task/rules${causeCode ? `?cause_code=${causeCode}` : ""}`);
export const seedDeadlineRules = () => post<DeadlineRule[]>("/task/rules/seed");

// ---------------------------------------------------------------- 法律咨询
//
// ★ 原来这里是「计时（Step 16）」—— 整套工时：计时器、补录、审批、
//   漏记反查、取整口径。**已经整个拆掉。**
//   提成制律所不按工时给客户开账单，团队成员和助理是工资制/分成制。
//   真正按小时收钱的只有法律咨询，所以只剩下面这一点。

export type Consult = {
  id: number;
  user_id: number;
  client_id: number;
  case_id: number | null;
  happened_on: string;
  minutes: number;
  /** 按半小时取整之后、真正计费的分钟数 */
  billable_minutes: number;
  channel: string;
  channel_label: string;
  summary: string;
  hourly_rate: string;
  amount: string;
  free: boolean;
  free_reason: string;
  status: string;
  status_label: string;
  invoice_no: string;
};

export type ConsultRate = {
  hourly_rate: string;
  /** 取整粒度：半小时 */
  increment_minutes: number;
  /** 低于这个时长不收费 */
  min_billable_minutes: number;
};

export const CHANNEL_LABEL: Record<string, string> = {
  office: "来所面谈", phone: "电话", online: "线上", visit: "外出",
};

export const fetchConsults = (opts: {
  client_id?: number; case_id?: number;
  since?: string; until?: string; status?: string;
} = {}) => {
  const q = new URLSearchParams();
  if (opts.client_id) q.set("client_id", String(opts.client_id));
  if (opts.case_id) q.set("case_id", String(opts.case_id));
  if (opts.since) q.set("since", opts.since);
  if (opts.until) q.set("until", opts.until);
  if (opts.status) q.set("status", opts.status);
  const qs = q.toString();
  return api<Consult[]>(`/consult/consults${qs ? "?" + qs : ""}`);
};

export const createConsult = (body: {
  client_id: number; happened_on: string; minutes: number;
  summary?: string; channel?: string; case_id?: number | null;
  hourly_rate?: string; free?: boolean; free_reason?: string;
}) => post<Consult>("/consult/consults", body);

export const voidConsult = (id: number, reason: string) =>
  post<Consult>(`/consult/consults/${id}/void?reason=${encodeURIComponent(reason)}`, {});

export const fetchConsultRate = () => api<ConsultRate>("/consult/rate");

export const fetchClientConsults = (clientId: number) =>
  api<{ count: number; minutes: number; amount: string; free_count: number;
        items: Consult[] }>(`/consult/clients/${clientId}/summary`);

// ---------------------------------------------------------------- 账单（Step 17）

export type FeeRate = {
  id: number; position_code: string; user_id: number | null;
  hourly_rate: string; currency: string;
  valid_from: string; valid_to: string | null; note: string;
};

export type Engagement = {
  id: number; case_id: number; client_id: number | null;
  mode: string; mode_label: string;
  fixed_amount: string;
  /** 0.2000 = 20% */
  contingency_rate: string;
  contingency_pct: string;
  contingency_base: string;
  base_amount: string;
  cap_amount: string;
  consult_rate: string;
  /** ★ 报价含不含税必须明确 —— 这是律所收费纠纷的前三名 */
  tax_included: boolean;
  vat_rate: string; vat_pct: string; currency: string;
  contract_no: string; signed_on: string;
  /** ★ 签约时系统给出的合法性提示，原样留着 —— 事后能证明提示过 */
  legality_note: string;
  confirmed: boolean;
  note: string; active: boolean;
};

export type Invoice = {
  id: number; number: string; case_id: number | null;
  client_id: number | null; engagement_id: number | null;
  period_from: string; period_to: string;
  issued_on: string; due_on: string;
  fee_amount: string; disbursement_amount: string; discount_amount: string;
  net_amount: string; tax_amount: string; total_amount: string;
  paid_amount: string; outstanding: string;
  vat_rate: string; vat_pct: string; currency: string;
  status: string; status_label: string;
  /** 已开出/已收款/已红冲的改不动 */
  locked: boolean;
  void_reason: string; note: string;
};

export type InvoiceLine = {
  id: number; invoice_id: number; kind: string; kind_label: string;
  description: string; user_id: number | null;
  minutes: number; hours: string; rate: string; amount: string;
  worked_on: string; source_entry_ids: string;
};

export type BillingPayment = {
  id: number; invoice_id: number; amount: string; paid_on: string;
  method: string; method_label: string; reference: string; note: string;
  reversed: boolean; reverse_reason: string;
};

/** ★ 风险代理能不能做 —— 法定，不是所里的规矩 */
export type ContingencyCheck = {
  /** block 整类禁止 / confirm 部分禁止要确认 / allow 未匹配到禁区 */
  level: string;
  basis: string; reason: string; caveat: string; message: string;
  cause_code: string; cause_name: string;
  max_rate: string; rate_ok: boolean;
};

export type CaseBilling = {
  case_id: number;
  engagement: Engagement | null;
  invoice_count: number;
  billed: string; paid: string; outstanding: string;
  draft_count: number;
};

export type Receivables = {
  total_outstanding: string; overdue_amount: string; invoice_count: number;
  aging: { label: string; count: number; amount: string }[];
  items: {
    id: number; number: string; case_id: number | null;
    issued_on: string; due_on: string;
    total: string; paid: string; outstanding: string;
    age_days: number; overdue: boolean;
  }[];
};

/** ★ 顺序＝界面下拉的顺序＝默认值。固定收费排第一是刻意的，见 billing/models.py */
export const FEE_MODE_LABEL: Record<string, string> = {
  fixed: "固定收费（包干）", contingency: "风险代理",
  hybrid: "混合（基础费＋风险）", retainer: "常年顾问费",
  hourly: "按次咨询计费",
};

export const INVOICE_STATUS_LABEL: Record<string, string> = {
  draft: "草稿", issued: "已开出", partial: "部分回款",
  paid: "已结清", void: "已红冲",
};

export const LINE_KIND_LABEL: Record<string, string> = {
  fee: "律师费", disbursement: "代垫费用", discount: "折让",
};

export const PAY_METHOD_LABEL: Record<string, string> = {
  transfer: "银行转账", cash: "现金", check: "支票",
  offset: "抵扣", other: "其他",
};

export const fetchRates = (on = "") =>
  api<FeeRate[]>(`/billing/rates${on ? "?on=" + on : ""}`);
export const createRate = (body: {
  hourly_rate: string; position_code?: string; user_id?: number | null;
  valid_from?: string | null; valid_to?: string | null; note?: string;
}) => post<FeeRate>("/billing/rates", body);

export const checkContingency = (caseId: number, rate: number) =>
  api<ContingencyCheck>(`/billing/cases/${caseId}/contingency-check?rate=${rate}`);
export const fetchEngagement = (caseId: number) =>
  api<Engagement | null>(`/billing/cases/${caseId}/engagement`);
export const createEngagement = (body: Record<string, unknown>) =>
  post<Engagement>("/billing/engagements", body);

export const fetchCaseInvoices = (caseId: number) =>
  api<Invoice[]>(`/billing/cases/${caseId}/invoices`);
export const fetchCaseBilling = (caseId: number) =>
  api<CaseBilling>(`/billing/cases/${caseId}/summary`);
export const createInvoice = (body: {
  case_id?: number | null; client_id?: number | null;
  period_from?: string | null; period_to?: string | null;
  due_on?: string | null; note?: string;
}) => post<Invoice>("/billing/invoices", body);
export const fetchInvoiceLines = (id: number) =>
  api<InvoiceLine[]>(`/billing/invoices/${id}/lines`);
export const addInvoiceLine = (id: number, body: {
  description: string; amount: string; kind?: string;
}) => post<InvoiceLine>(`/billing/invoices/${id}/lines`, body);
export const removeInvoiceLine = (lineId: number) =>
  api<Invoice>(`/billing/lines/${lineId}`, { method: "DELETE" });
export const pullConsultIntoInvoice = (id: number, body: {
  since?: string | null; until?: string | null; client_id?: number;
}) => post<{ pulled: number; lines: number; minutes: number; amount: string }>(
  `/billing/invoices/${id}/pull-consult`, body);
export const billFixed = (id: number, body: { amount?: string | null; description?: string }) =>
  post<InvoiceLine>(`/billing/invoices/${id}/bill-fixed`, body);
export const billContingency = (id: number, body: { recovered?: string | null; description?: string }) =>
  post<InvoiceLine>(`/billing/invoices/${id}/bill-contingency`, body);
export const issueInvoice = (id: number, body: { issued_on?: string | null; due_on?: string | null }) =>
  post<Invoice>(`/billing/invoices/${id}/issue`, body);
export const voidInvoice = (id: number, reason: string) =>
  post<Invoice>(`/billing/invoices/${id}/void`, { reason });

export const fetchPayments = (id: number) =>
  api<BillingPayment[]>(`/billing/invoices/${id}/payments`);
export const recordPayment = (id: number, body: {
  amount: string; paid_on?: string | null; method?: string;
  reference?: string; note?: string;
}) => post<BillingPayment>(`/billing/invoices/${id}/payments`, body);
export const reversePayment = (paymentId: number, reason: string) =>
  post<BillingPayment>(`/billing/payments/${paymentId}/reverse`, { reason });

export const fetchReceivables = () => api<Receivables>("/billing/receivables");

/** 本期开票 / 回款的逐笔流水。★ 存量看 receivables，流量看这个 */
export type RevenueFlow = {
  since: string; until: string;
  billed: { id: number; number: string; case_id: number | null;
            issued_on: string; amount: string; paid: string; status: string }[];
  billed_total: string;
  received: { id: number; paid_on: string; amount: string; method: string;
              invoice_id: number; number: string; case_id: number | null }[];
  received_total: string;
};
export const fetchRevenueFlow = (opts: {
  scope?: string; org_id?: number; since?: string; until?: string;
} = {}) => {
  const q = new URLSearchParams();
  if (opts.scope) q.set("scope", opts.scope);
  if (opts.org_id) q.set("org_id", String(opts.org_id));
  if (opts.since) q.set("since", opts.since);
  if (opts.until) q.set("until", opts.until);
  const qs = q.toString();
  return api<RevenueFlow>(`/billing/revenue-flow${qs ? "?" + qs : ""}`);
};

/** 金额显示：加千分位。★ 律师费动辄六位数，不分节没法读 */
export function yuan(v: string | number): string {
  const n = Number(v || 0);
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2,
                                     maximumFractionDigits: 2 });
}

// ---------------------------------------------------------------- 单据流转（Step 18）

export type Ticket = {
  id: number; number: string; kind: string; kind_label: string;
  /** ★ 这一栏财务、行政、用印管理都看得到 —— 后端会拦住写案件名的 */
  subject: string;
  amount: string; currency: string;
  payee: string;
  /** 只有有付款权限的人看得到完整账号，其余人看到掩码 */
  payee_account: string;
  account_masked: boolean;
  need_by: string; detail: string; doc_ids: string;
  status: string; status_label: string; locked: boolean;
  current_seq: number;
  requested_by: number; requested_at: string;
  paid_by: number | null; paid_on: string; pay_reference: string;
  close_note: string; disbursement_id: number | null;
  seal_kind: string; seal_kind_label: string; seal_document: string;
  seal_copies: number; seal_used: number; seal_takeaway: boolean;
  /** ★ 隔离墙：看不见关联案件时 case_id 为 null，且 case_note 会明说 */
  case_id: number | null;
  case_visible: boolean;
  case_note: string;
  steps?: TicketStep[];
};

export type TicketStep = {
  id: number; seq: number; name: string; required_perm: string;
  status: string; status_label: string;
  decided_by: number | null; decided_at: string; note: string;
};

export type FlowRule = {
  id: number; kind: string; kind_label: string; name: string;
  min_amount: string; max_amount: string;
  steps: string;
  step_list: { name: string; perm: string }[];
  active: boolean; note: string;
};

export const TICKET_KIND_LABEL: Record<string, string> = {
  payment: "请款（垫付）", reimburse: "报销", seal: "用印",
  disburse: "付款给第三方", other: "其他",
};

export const TICKET_STATUS_LABEL: Record<string, string> = {
  draft: "草稿", pending: "审批中", approved: "已批待付",
  rejected: "已驳回", paid: "已付款", done: "已完成", cancelled: "已作废",
};

export const SEAL_KIND_LABEL: Record<string, string> = {
  firm: "公章", contract: "合同专用章", legal_person: "法定代表人章",
  finance: "财务专用章", other: "其他",
};

export const createTicket = (body: Record<string, unknown>) =>
  post<Ticket>("/approval/tickets", body);
export const submitTicket = (id: number) =>
  post<Ticket>(`/approval/tickets/${id}/submit`, {});
export const fetchTicket = (id: number) => api<Ticket>(`/approval/tickets/${id}`);
export const decideTicket = (id: number, approve: boolean, note = "") =>
  post<Ticket>(`/approval/tickets/${id}/decide`, { approve, note });
export const payTicket = (id: number, body: { paid_on?: string | null; reference?: string }) =>
  post<Ticket>(`/approval/tickets/${id}/pay`, body);
export const executeSeal = (id: number, used_copies: number, note = "") =>
  post<Ticket>(`/approval/tickets/${id}/seal`, { used_copies, note });
export const cancelTicket = (id: number, reason: string) =>
  post<Ticket>(`/approval/tickets/${id}/cancel`, { reason });

export const fetchMyTickets = (status = "") =>
  api<Ticket[]>(`/approval/mine${status ? "?status=" + status : ""}`);
export const fetchInbox = () => api<Ticket[]>("/approval/inbox");
export const fetchCaseTickets = (caseId: number) =>
  api<Ticket[]>(`/approval/cases/${caseId}/tickets`);
export const fetchFlows = (kind = "") =>
  api<FlowRule[]>(`/approval/flows${kind ? "?kind=" + kind : ""}`);
export const seedFlows = () => post<FlowRule[]>("/approval/flows/seed", {});

// ---------------------------------------------------------------- 归档

export type InventoryGroup = {
  module: string; name: string; count: number; note: string;
};

export type Inventory = {
  case_id: number;
  groups: InventoryGroup[];
  item_count: number;
  /** ★ 装着却没报数的模块 —— 卷少了一块，必须明说 */
  skipped: string[];
};

export type ArchiveBlocker = { module: string; what: string; how: string };

export type ArchiveRecord = {
  id: number; case_id: number; volume_no: string;
  groups: InventoryGroup[];
  item_count: number;
  fingerprint: string;
  archived_by: number; archived_at: string;
  keep_until: string; note: string;
  active: boolean;
  unarchived_by: number | null; unarchived_at: string;
  unarchive_reason: string;
};

export type ArchiveVerify = {
  volume_no: string; ok: boolean;
  fingerprint_then: string; fingerprint_now: string;
  item_count_then: number; item_count_now: number;
  message: string;
};

export const fetchInventory = (caseId: number) =>
  api<Inventory>(`/archive/cases/${caseId}/inventory`);
export const fetchArchiveBlockers = (caseId: number) =>
  api<ArchiveBlocker[]>(`/archive/cases/${caseId}/blockers`);
export const fetchArchiveRecord = (caseId: number) =>
  api<ArchiveRecord | Record<string, never>>(`/archive/cases/${caseId}/record`);
export const fetchArchiveHistory = (caseId: number) =>
  api<ArchiveRecord[]>(`/archive/cases/${caseId}/history`);
export const doArchive = (caseId: number, body: {
  note?: string; keep_years?: number; force?: boolean;
}) => post<ArchiveRecord>(`/archive/cases/${caseId}/archive`, body);
export const doUnarchive = (caseId: number, reason: string) =>
  post<ArchiveRecord>(`/archive/cases/${caseId}/unarchive`, { reason });
export const verifyArchive = (caseId: number) =>
  api<ArchiveVerify>(`/archive/cases/${caseId}/verify`);

// ---------------------------------------------------------------- 引用体检

export type RecheckItem = {
  citation_id: number; ordinal: number; source_id: string; title: string;
  /** ok / changed / expired / gone / unknown */
  state: string; state_label: string; note: string;
  /** 当时冻结的原文 —— changed/gone 时这是唯一的退路 */
  snippet_then: string;
  snippet_now: string;
  status_then: string; status_now: string;
  retrieved_at: string;
};

export type Recheck = {
  doc_kind: string; doc_id: number;
  total: number; checked_at: string; corpus_version: string;
  items: RecheckItem[];
  ok: number; changed: number; expired: number; gone: number; unknown: number;
};

export const recheckCitations = (docKind: string, docId: number) =>
  api<Recheck>(`/citation/${docKind}/${docId}/recheck`);

// ---------------------------------------------------------------- 看板（Step 21）

export type MetricDef = {
  key: string; name: string; unit: string;
  description: string;
  /** 点开去哪。空 = 这个数点不开 */
  drill: string;
};

export type MetricValue = {
  value: string | number;
  text: string;
  detail?: { label: string; value: number }[];
};

export type LeadRevenue = {
  user_id: number;
  billed: string; received: string; outstanding: string; cases: number;
};

export type CaseRevenue = {
  case_id: number; billed: string; received: string; outstanding: string;
};

export type Overview = {
  scope: string;
  since: string; until: string;
  values: Record<string, MetricValue>;
  /** ★ 没报上来的模块。界面要标"查不到"，**不能显示 0** */
  missing: string[];
  catalog: MetricDef[];
  by_lead?: { suppressed: boolean; reason: string; rows: LeadRevenue[];
              total?: { billed: string; received: string } };
  at_risk?: CaseRevenue[];
};

/** 统一待办中心的一条 —— 各模块自报，analytics 汇总 */
export type InboxItem = {
  key: string;
  kind: string;          // deadline / task / ticket
  title: string;
  detail: string;
  /** 空串 = **算不出**，不是"没有期限" */
  due_on: string;
  urgency: string;       // expired / unknown / urgent / normal
  link: string;
  case_id: number | null;
  /** 为什么这件事轮到我 */
  why_me: string;
};

export type Inbox = {
  items: InboxItem[];
  total: number;
  by_urgency: Record<string, number>;
  by_kind: Record<string, number>;
  /** ★ 哪个模块没报上来 —— 必须显示，否则人会以为自己看全了 */
  missing: string[];
  asked: string[];
};

export const URGENCY_LABEL: Record<string, string> = {
  expired: "已过期", unknown: "算不出", urgent: "临近", normal: "正常",
};
export const INBOX_KIND_LABEL: Record<string, string> = {
  deadline: "时限", task: "任务", ticket: "单据",
};

export const fetchInboxCenter = () => api<Inbox>("/analytics/inbox");

export const fetchOverview = (opts: {
  scope?: string; org_id?: number; since?: string; until?: string;
} = {}) => {
  const q = new URLSearchParams();
  if (opts.scope) q.set("scope", opts.scope);
  if (opts.org_id) q.set("org_id", String(opts.org_id));
  if (opts.since) q.set("since", opts.since);
  if (opts.until) q.set("until", opts.until);
  return api<Overview>(`/analytics/overview${q.toString() ? "?" + q : ""}`);
};
