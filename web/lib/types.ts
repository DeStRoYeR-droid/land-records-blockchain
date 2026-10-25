/**
 * Shapes returned by the gateway (python -m gateway). They mirror what the
 * chaincode (chaincode/landrec/landrec.py) and the validator (agent/) store.
 */

export type MspId = "Org1MSP" | "Org2MSP" | "Org3MSP";

export type AppStatus =
  | "SUBMITTED"
  | "WITH_REVENUE"
  | "DOCS_VERIFIED"
  | "FLAGGED"
  | "SURVEYED"
  | "WITH_COLLECTORATE"
  | "APPROVED"
  | "REJECTED";

export type ParcelStatus = "ACTIVE" | "TRANSFER_PENDING";
export type Verdict = "clear" | "attention" | "flagged";
export type OfficerRole = "registrar" | "clerk" | "verifier" | "surveyor" | "collector";

export interface TrailEntry {
  at: string;
  by: string;
  org: MspId;
  role: string;
  action: string;
  status: AppStatus | null;
  txId: string;
}

export interface AnchoredDocument {
  sha256: string;
  docId: string;
  documentType: string;
  issuer: string;
}

export interface ValidationAnchor {
  sha256: string;
  appId: string;
  reportCid: string;
  traceCid: string;
  verdict: Verdict;
  flagCount: number;
  confidence: number;
  agent: string;
  agentDid: string;
  pgpFingerprint: string;
  anchoredAt: string;
  txId: string;
}

export interface Application {
  appId: string;
  kind: string;
  surveyNo: string;
  sellerName: string;
  sellerRef: string;
  buyerName: string;
  buyerRef: string;
  considerationINR: number;
  status: AppStatus;
  consent?: {
    intentId: string;
    intentSha256: string;
    sellerDid: string;
    buyerDid: string;
    verifiedAt: string;
  };
  documents: AnchoredDocument[];
  verification?: {
    verifiedBy: string;
    at: string;
    result: "MATCH" | "MISMATCH";
    documentsChecked: number;
    mismatchedDocuments: string[];
    unexpectedFingerprints: string[];
  } | null;
  survey?: {
    surveyedBy: string;
    at: string;
    recordedAreaSqm: number;
    measuredAreaSqm: number;
    deviationPct: number;
    tolerancePct: number;
    withinTolerance: boolean;
    boundaries: string;
  } | null;
  decision?: { by: string; at: string; outcome: "APPROVED" | "REJECTED"; remarks: string } | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  trail: TrailEntry[];
  validations: ValidationAnchor[];
  myRole?: "seller" | "buyer";
}

export interface Parcel {
  surveyNo: string;
  village: string;
  district: string;
  areaSqm: number;
  ownerName: string;
  ownerRef: string;
  status: ParcelStatus;
  pendingApplication: string | null;
  lastTransfer: { appId: string; fromName: string; toName: string; at: string; txId: string } | null;
  registeredBy: string;
  registeredAt: string;
  updatedAt: string;
  boundary?: GeoPolygon | null;
  boundaryAreaSqm?: number;
  perimeterM?: number;
  centroid?: [number, number];
  corners?: number;
}

export interface PublicParcel {
  surveyNo: string;
  village: string;
  district: string;
  areaSqm: number;
  status: ParcelStatus;
  ownerInitials: string;
  registeredAt: string | null;
  updatedAt: string | null;
  lastTransferAt: string | null;
  boundary?: GeoPolygon | null;
  boundaryAreaSqm?: number | null;
  centroid?: [number, number] | null;
  perimeterM?: number | null;
}

// ---- the geospatial database ---------------------------------------------------

export interface GeoPolygon {
  type: "Polygon";
  coordinates: [number, number][][];
}

export interface ParcelFeature {
  type: "Feature";
  id: string;
  geometry: GeoPolygon;
  properties: {
    surveyNo: string;
    village: string;
    district: string;
    status: ParcelStatus;
    areaSqm: number;
    boundaryAreaSqm: number;
    perimeterM: number;
    centroid: [number, number];
    ownerInitials: string;
    owners: number;
    transfers: number;
    registeredAt: string | null;
    lastTransferAt: string | null;
    distanceM?: number;
    sharedBoundaryM?: number;
    overlapSqm?: number;
    overlapPctOfThis?: number;
  };
}

export interface ParcelCollection {
  type: "FeatureCollection";
  features: ParcelFeature[];
  count?: number;
}

export interface GeoStats {
  format: string;
  file: string;
  bytes: number;
  srs: string;
  spatialIndex: string;
  indexedRows: number;
  parcels: number;
  totalAreaSqm: number;
  villages: number;
  byVillage: { village: string; parcels: number; areaSqm: number }[];
  byStatus: Record<string, number>;
  extent: { minLng: number; minLat: number; maxLng: number; maxLat: number } | null;
  syncedToBlock: number | null;
  lastError: string | null;
}

export interface BoundaryCheck {
  boundary: GeoPolygon;
  boundaryAreaSqm: number;
  perimeterM: number;
  centroid: [number, number];
  corners: number;
  areaDeviationPct: number | null;
  areaWithinTolerance: boolean;
  overlaps: ParcelFeature[];
  touching: string[];
}

// ---- chain of ownership and transfer records ----------------------------------

export interface OwnershipLink {
  seq: number;
  owner: string;
  since: string;
  until: string | null;
  acquired: "registration" | "transfer";
  appId: string | null;
  txId: string;
  block: number | null;
  decidedBy?: string | null;
}

export interface TransferSummary {
  appId: string;
  status: AppStatus;
  statusText: string;
  openedAt: string | null;
  updatedAt: string;
  seller: string;
  buyer: string;
  decision: string | null;
  decidedAt: string | null;
  verdicts: Verdict[];
  publicRecord: string;
}

export interface Ownership {
  surveyNo: string;
  owners: number;
  links: OwnershipLink[];
  transfers: TransferSummary[];
}

export interface RecordVerification {
  valid: boolean;
  reason: string;
  fingerprint: string | null;
  signedAt: number | null;
  record: { recordId: string; copy: "public" | "party"; issuedAt: string } | null;
  applicationId: string | null;
  keyAttestedOnLedger: boolean;
  attestation: { pgpFingerprint: string; attestedBy: string; attestedAt: string } | null;
  currentStatus?: AppStatus | null;
}

export interface PublicParcelEvent {
  kind: "registered" | "pending" | "transferred" | "released";
  at: string;
  txId: string;
  appId?: string | null;
  ownerInitials?: string;
}

export interface AgentPublic {
  online: boolean;
  name?: string;
  explainer?: { engine: string; model: string | null };
  framework?: AgentStatus["framework"] | null;
  pgpFingerprint?: string;
  pgpFingerprintFormatted?: string;
  attested?: boolean;
  counters?: AgentCounters;
  listening?: boolean;
}

export interface AgentCounters {
  runs: number;
  clear: number;
  attention: number;
  flagged: number;
  failed: number;
  skipped: number;
  avgDurationMs: number | null;
}

export interface PublicSummary {
  parcels: number;
  villages: number;
  applications: number;
  byStatus: Partial<Record<AppStatus, number>>;
  height: number | null;
  reports: number;
  verdicts: Partial<Record<Verdict, number>>;
  recentDecisions: { surveyNo: string; outcome: "APPROVED" | "REJECTED"; at: string }[];
  agent: AgentPublic;
}

export interface Officer {
  username: string;
  org: string;
  title: string;
  duty: string;
  role: OfficerRole;
  mspId: MspId;
  dept?: string;
  certificate?: Record<string, unknown>;
}

export interface CitizenClaims {
  kind: "citizen";
  digilockerId: string;
  ownerRef: string;
  did: string;
  name: string;
}

export interface TransferIntent {
  type: "landchain.transfer-intent/v1";
  intentId: string;
  surveyNo: string;
  sellerRef: string;
  buyerRef: string;
  considerationINR: number;
  createdAt: string;
}

export interface IntentRecord {
  id: string;
  status: "awaiting-buyer" | "ready" | "filed" | "cancelled";
  intent: TransferIntent;
  sellerName: string;
  buyerName: string;
  sellerDigilockerId: string;
  buyerDigilockerId: string;
  sellerSignature: { did: string; signature: string };
  buyerSignature: { did: string; signature: string } | null;
  createdAt: string;
  acceptedAt?: string;
  appId?: string;
  filedBy?: string;
  filedAt?: string;
}

export interface LockerDocument {
  docId: string;
  docType: string;
  orgName: string;
  description: string;
  content?: string;
  contentSha256: string;
  issuedAt: string;
  anchored?: boolean;
}

export interface AccessLogEntry {
  at: string;
  department: string;
  action: string;
  holder: string | null;
  docId: string | null;
  purpose: string | null;
}

// ---- the validator ----------------------------------------------------------

export type StepKind = "decision" | "tool" | "check" | "llm" | "measure" | "verify" | "store" | "sign" | "anchor";
export type StepStatus = "info" | "ok" | "pass" | "warn" | "fail" | "skipped" | "error" | "running";

export interface RunStep {
  seq: number;
  kind: StepKind;
  name: string;
  label: string;
  reason: string;
  status: StepStatus;
  input: unknown;
  output: unknown;
  startedAt: string;
  ms: number;
}

export interface CheckResult {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "skipped";
  detail: string;
  evidence: string[];
}

export interface VerdictResult {
  status: Verdict;
  flagCount: number;
  failed: number;
  attention: number;
  passed: number;
  skipped: number;
  confidence: number;
  rule: string;
}

export interface WriterMeta {
  text: string;
  engine: "omlx" | "template" | string;
  model: string | null;
  grounded: boolean;
  rejectedTokens: string[];
  fallbackReason: string | null;
  latencyMs: number | null;
  drafts?: number;
  faithfulness?: number | null;
  hallucinationRate?: number | null;
}

export interface MeasureScores {
  entity: number;
  sentenceSupport: number;
  consistency: number;
  coverage: number;
  faithfulness: number;
  hallucinationRate: number;
}

export interface MeasureSummary {
  version: string;
  passed: boolean;
  threshold: number;
  scores: MeasureScores;
  claims: { total: number; supported: number };
  hardViolations: string[];
  sentences: { text: string; score: number; supportedBy: string[]; weak: boolean }[];
}

export interface GenerationRecord {
  writer: string;
  model: string | null;
  drafts: number;
  fallbackReason: string | null;
  attempts: { attempt: number; writer: string; passed: boolean; faithfulness: number; violations: string[] }[];
  tokensPerSecond: number | null;
  completionTokens: number | null;
  latencyMs: number | null;
}

export interface JudgeRecord {
  ran: boolean;
  usable?: boolean;
  reason?: string;
  agrees?: boolean;
  unsupportedSentences?: number[];
  measurerWeakSentences?: number[];
  ms?: number;
}

export interface QualityRecord {
  aggregate: { minFaithfulness: number; maxHallucinationRate: number; retries: number; fallbacks: number; modelWritten: number };
  hallucination: Record<"officer" | "citizen", MeasureSummary>;
  generation: Record<"officer" | "citizen", GenerationRecord>;
  judge: Record<"officer" | "citizen", JudgeRecord | null>;
  promptInjection: { hits: number; promptSanitised: boolean };
  reflections?: Record<"officer" | "citizen", Reflection[]>;
}

export interface Reflection {
  attempt: number;
  action: "redraft" | "template";
  fix: string;
  chosenBy: "model" | "rule";
  note?: string;
  ms?: number;
}

// ---- the model's own investigation ---------------------------------------------

export interface AgentTurn {
  turn: number;
  thought: string;
  requested: { name: string; args: Record<string, unknown> }[];
  ms: number;
  lastTurn?: boolean;
}

export interface AgentCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  ms: number;
  observation?: unknown;
}

export interface AgentFinding {
  claim: string;
  severity: "concern" | "info";
  evidence: string[];
  refusedBecause?: string[];
}

export interface Investigation {
  ran: boolean;
  reason?: string | null;
  summary?: string;
  stop?: string;
  findings: AgentFinding[];
  refused: AgentFinding[];
  turns: AgentTurn[];
  calls: AgentCall[];
  errors?: number;
  protocol?: "native" | "text";
  ms?: number;
  model?: string | null;
  brief?: string;
  budget?: { maxCalls: number; maxTurns: number };
}

export interface VerifierFinding {
  id: string;
  label: string;
  result: "pass" | "fail" | "skip";
  detail: string;
}

export interface VerificationRecord {
  pre: { passed: boolean; summary: string; findings: VerifierFinding[] };
  post: { passed: boolean; findings: (VerifierFinding & { ok?: boolean })[] } | null;
}

export type RunStatus = "queued" | "running" | "signed" | "anchored" | "failed" | "skipped" | "quarantined";

export interface AgentRun {
  runId: string;
  appId: string;
  surveyNo?: string;
  trigger: { kind: "event" | "manual" | "catch-up" | string; event?: string; block?: number; txId?: string; requestedBy?: string };
  status: RunStatus;
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  resumed?: boolean;
  steps: RunStep[];
  stepCount?: number;
  verdict?: VerdictResult;
  checks?: CheckResult[];
  explanation?: { officer: WriterMeta; citizen: WriterMeta };
  quality?: QualityRecord;
  investigation?: Investigation;
  verification?: VerificationRecord;
  timings?: Record<string, number>;
  visited?: string[];
  report?: {
    reportId: string;
    sha256: string;
    cid: string;
    traceCid: string;
    signatureCid: string;
    certificateCid?: string;
    certificateSignatureCid?: string;
    bytes: number;
    schema?: string;
  };
  anchor?: { txId: string; block: number };
  error?: string;
}

export interface AgentStatus {
  name: string;
  software: string;
  startedAt: string;
  identity: {
    name: string;
    mspId: string;
    role: string;
    x509Id: string;
    pgpFingerprint: string;
    pgpFingerprintFormatted: string;
    keyId: string;
    userId: string;
    created: number;
    did: string;
    publicKey: string;
    algorithm: string;
  };
  onChainKey: {
    pgpFingerprint?: string;
    did?: string;
    status: string;
    attestedBy?: string;
    attestedAt?: string;
    matchesLocalKey?: boolean;
    error?: string;
  };
  explainer: {
    engine: string;
    mode: string;
    reachable: boolean;
    baseUrl: string;
    model: string | null;
    models: string[];
    lastError: string | null;
    lastLatencyMs: number | null;
    framework?: string;
    apiKeyConfigured?: boolean;
    avgTokensPerSecond?: number | null;
  };
  framework?: {
    orchestration: string;
    llm: string;
    checkpointer: string;
    nodes: number;
    judge: string;
    faithfulnessMin: number;
    draftAttempts: number;
  };
  listener: { peer: string; connected: boolean; lastBlock: number | null; lastEventAt: string | null; handoffsSeen: number };
  queue: { depth: number; queued: string[]; current: { runId: string; appId: string; startedAt?: string } | null };
  counters: AgentCounters;
  placement: string;
  authority: string;
}

export interface GraphDescription {
  name: string;
  version: string;
  libraries: Record<string, string>;
  nodes: { id: string; label: string }[];
  edges: { from: string; to: string; conditional: boolean }[];
  mermaid: string;
  subgraphs?: Record<string, { name: string; nodes: string[]; edges: { from: string; to: string; conditional: boolean }[]; mermaid: string; tools: string[]; maxCalls: number; maxTurns: number }>;
}

export interface AgentMetrics {
  runsMeasured: number;
  texts: number;
  faithfulness: {
    mean: number | null;
    min: number | null;
    officerMean: number | null;
    citizenMean: number | null;
    histogram: { from: number; to: number; count: number }[];
  };
  hallucinationRate: { mean: number | null; max: number | null };
  writers: { omlx: number; template: number; modelAcceptedShare: number | null };
  retries: number;
  fallbacks: number;
  promptInjectionHits: number;
  judge: { compared: number; agreementShare: number | null };
  model: { samples: number; avgTokensPerSecond: number | null; medianTokensPerSecond: number | null };
  verification: { runs: number; independentPassShare: number | null; quarantined: number; ledgerConfirmed: number };
  nodeLatencyMs: Record<string, { avg: number; max: number; runs: number }>;
  recent: {
    runId: string;
    appId: string;
    at: string | null;
    verdict: Verdict | null;
    minFaithfulness: number;
    retries: number;
    fallbacks: number;
    durationMs: number | null;
    status: string;
  }[];
  agentic?: {
    investigations: number;
    toolCalls: number;
    avgToolCalls: number | null;
    invalidCalls: number;
    toolUse: Record<string, number>;
    stops: Record<string, number>;
    findingsAccepted: number;
    findingsRefused: number;
    concerns: number;
    groundedShare: number | null;
    filesRaisedOnlyByAgent: number;
    reflections: number;
    reflectChoices: { redraft: number; template: number };
    reflectByModel: number;
  };
}

export interface AgentKeyRecord {
  agentName: string;
  pgpFingerprint: string;
  did: string;
  publicKey: string;
  status: string;
  attestedBy: string;
  attestedAt: string;
  previousKeys: { pgpFingerprint: string; did: string; attestedBy: string; attestedAt: string; rotatedAt: string }[];
}

export interface AgentEvent {
  type: string;
  at?: string;
  runId?: string;
  appId?: string;
  [key: string]: unknown;
}

// ---- the network -----------------------------------------------------------

export interface NetworkNode {
  name: string;
  kind: "orderer" | "peer" | "ca" | "service";
  org: string | null;
  port: number;
  up: boolean;
  height?: number;
  state?: string;
  term?: number;
  leader?: string | null;
  commitIndex?: number;
  lastLogIndex?: number;
  msSinceLeaderContact?: number | null;
  events?: { at: number; term: number; text: string }[];
  pendingTx?: number;
  lastBlockHash?: string;
  lastCommitAgoS?: number | null;
  stats?: Record<string, number>;
  recentInvalid?: { block: number; txId: string; fn: string; code: string; detail: string }[];
  deliverSource?: string;
  worldStateKeys?: number;
  chaincode?: { version?: string; sequence?: number; matches?: boolean; policy?: string };
  txCodes?: Record<string, number>;
}

export interface NetworkView {
  channel: string;
  leader: string | null;
  height: number | null;
  nodes: NetworkNode[];
  checkedAt: string;
}

export interface BlockTx {
  txId: string | null;
  index: number;
  fn: string | null;
  timestamp: string | null;
  code: string | null;
  detail: string;
  creator: { mspId: string; name: string; role: string | null; ou: string | null } | null;
  event: { name: string; payload?: unknown } | null;
}

export interface BlockSummary {
  number: number;
  hash: string;
  previousHash: string;
  kind: string;
  orderer: string | null;
  raft: { term?: number; index?: number } | null;
  txCount: number;
  validCount: number;
  timestamp: string | null;
  txs: BlockTx[];
}

export interface LiveBlockEvent {
  type: "block";
  number: number;
  hash?: string;
  kind?: string;
  orderer?: string | null;
  txs: { txId?: string; fn: string | null; code: string | null; timestamp?: string; event?: string | null; by?: string | null; mspId?: string | null; role?: string | null }[];
}

// ---- officer desk -----------------------------------------------------------

export interface OfficerSummary {
  byStatus: Partial<Record<AppStatus, number>>;
  total: number;
  queue: {
    appId: string;
    surveyNo: string;
    status: AppStatus;
    action: string;
    seller: string;
    buyer: string;
    updatedAt: string;
    validation: Verdict | null | undefined;
  }[];
  readyIntents: number;
  recent: { appId: string; status: AppStatus; surveyNo: string; updatedAt: string; last: TrailEntry | null }[];
}

export interface ApplicationDetail {
  application: Application;
  parcel: Parcel;
  versions: number;
  agentRuns: (Omit<AgentRun, "steps" | "checks"> & { stepCount?: number })[];
}

export interface BlocksPage {
  height: number;
  blocks: BlockSummary[];
}

export interface SellerDocument {
  docId: string;
  docType: string;
  orgName: string;
  description: string;
  issuedAt: string;
  contentSha256: string;
  anchored: boolean;
}

// ---- citizen portal ---------------------------------------------------------

export interface CitizenMe {
  profile: CitizenClaims & { onChain: { did: string; ownerRef?: string; registeredAt?: string } | null };
  parcels: Parcel[];
  applications: Application[];
  intents: IntentRecord[];
}

export interface CitizenReport {
  runId: string;
  verdict?: VerdictResult;
  report?: AgentRun["report"];
  anchor?: AgentRun["anchor"];
  finishedAt?: string;
  citizenExplanation?: string | null;
  citizenWriter?: { engine?: string | null; model?: string | null; faithfulness?: number | null; drafts?: number | null };
  quality?: QualityRecord["aggregate"] | null;
  verification?: { independent?: boolean | null; ledger?: boolean | null };
  hasCertificate?: boolean;
  checks: { label: string; status: CheckResult["status"]; detail: string }[];
}
