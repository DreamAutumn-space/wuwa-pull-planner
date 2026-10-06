export const DIFFICULTIES = ['低', '中低', '中', '中高', '高'] as const
export type Difficulty = (typeof DIFFICULTIES)[number]

export const MODES = [
  ['single', '单队最高 DPS'],
  ['two_teams', '两队总 DPS'],
  ['four_teams', '四队总 DPS'],
  ['main_c', '指定主 C'],
  ['character', '指定角色所在队'],
  ['fixed_team', '指定队伍'],
] as const
export type OptimizationMode = (typeof MODES)[number][0]

export type CostMode = 'pulls' | 'gold'

export interface OwnedCharacter {
  character: string
  chain: number
  signature_refinement: number
}

export interface OwnedWeapon {
  id: string
  weapon: string
  refinement: number
}

export interface Account {
  characters: OwnedCharacter[]
}

export interface LegacyAccount {
  characters: Array<{
    character: string
    chain: number
    signature_refinement?: number
  }>
  weapons?: OwnedWeapon[]
}

export interface CharacterCatalogEntry {
  character: string
  aliases?: string[]
  signature_weapon?: string
  rarity?: 4 | 5
  is_limited?: boolean
  release_status?: string
  status?: string
  demo_only?: boolean
}

export interface CharacterCatalog {
  characters: CharacterCatalogEntry[]
  game_version?: string
  as_of?: string
  current_up?: { characters?: string[]; ends_at?: string; source?: string }
  upcoming_up?: { characters?: string[]; starts_at?: string | null }
  four_star_policy?: { default_chain?: number }
}

export interface ReferenceSection {
  id: string
  label: string
  normalized_y?: number
  y?: number
}

export interface ReferenceDps {
  image_url: string
  width: number
  height: number
  version: string
  sections: ReferenceSection[]
}

export interface DpsRecognitionCandidate {
  name: string
  score?: number | null
}

export interface DpsRecognitionCharacter {
  name: string | null
  unknown?: boolean
  candidates?: DpsRecognitionCandidate[]
  score?: number | null
  margin?: number | null
  source_rect?: DpsRecognitionRect
  /** Present only after a person checks the source image and named portrait. */
  identity_verification?: string | null
  automatic_name?: string | null
  automatic_unknown?: boolean
}

export interface DpsRecognitionCell {
  raw: string
  number?: number | null
  confidence?: number | null
}

export interface DpsRecognitionTableRow {
  raw_text?: string
  cells?: DpsRecognitionCell[]
  y?: number
}

export interface DpsRecognitionNote {
  text: string
  confidence?: number | null
}

export interface DpsRecognitionRect {
  x: number
  y: number
  width: number
  height: number
}

export interface DpsRecognitionPanel {
  id: string
  section_id: string
  source_rect: DpsRecognitionRect
  characters: DpsRecognitionCharacter[]
  difficulty?: string | null
  stability?: string | null
  table_rows?: DpsRecognitionTableRow[]
  tables?: Array<{ id: string; source_rect?: DpsRecognitionRect; table_rows?: DpsRecognitionTableRow[] }>
  notes?: DpsRecognitionNote[]
  optimizer_eligible?: boolean
  manual_review?: {
    tables?: DpsRecognitionManualReviewTable[]
  }
}

export interface DpsRecognitionManualReviewTable {
  title: string
  author?: string | null
  source_text?: string | null
  difficulty?: string | null
  stability?: string | null
  headers?: string[]
  sample_rows?: string[][]
  rows?: Array<Array<string | null>>
  numeric_verification?: string | null
  note?: string | null
}

export interface DpsRecognitionDocument {
  recognition_status: string
  source: {
    width: number
    height: number
    sha256?: string
    observed_version?: string
  }
  summary: {
    ocr_token_count?: number
    panel_count?: number
    table_count?: number
    avatar_slots?: number
    unknown_avatar_slots?: number
    automatic_unknown_avatar_slots?: number
    manually_checked_avatar_slots?: number
    visually_transcribed_table_count?: number
    visually_transcribed_row_count?: number
    optimizer_eligible_panel_count?: number
  }
  panels: DpsRecognitionPanel[]
  raw_ocr?: { reading_order_text?: string }
  manual_review?: { unit_note?: string }
}

export interface PortraitAtlasEntry {
  id: string
  canonical: string
  aliases?: string[]
  image_url: string
}

export interface PortraitAtlas {
  portraits: PortraitAtlasEntry[]
}

export interface TeamMember {
  character: string
  chain: number
  max_chain?: number
  weapon: string
  refinement: number
}

export interface Rotation {
  id: string
  difficulty: Difficulty
  dps: number
}

export interface TeamRecord {
  id: string
  main_c: string
  members: TeamMember[]
  rotations: Rotation[]
  /** Optional provenance retained in the client-side public/custom database. */
  source?: DpsRecordSource
}

export interface DpsRecordSource {
  table_id?: string
  row_index?: number
  label?: string
  title?: string
  headers?: string[]
  row?: Array<string | null>
  [key: string]: unknown
}

export interface DatabaseMetadata {
  unit?: string
  unit_note?: string
  mapped_table_count?: number
  mapped_table_ids?: string[]
  source_table_count?: number
  mapped_row_count?: number
  source_row_count?: number
  /** Unmapped source items; one entry can describe a whole table or a single row. */
  excluded_tables?: Array<{
    id?: string
    table_id?: string
    row_index?: number
    label?: string
    reason?: string
    row_count?: number
  }>
  rules?: {
    cumulative_upgrades?: boolean
    unspecified_signature_refinement?: number
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface Database {
  version: string
  records: TeamRecord[]
  metadata?: DatabaseMetadata
}

export interface OptimizeSettings {
  mode: OptimizationMode
  budget: number
  /** ``pulls`` uses expected pulls; ``gold`` treats every new copy as one gold. */
  cost_mode: CostMode
  max_difficulty: Difficulty
  allowed_difficulties?: Difficulty[]
  repeatable_healers: string[]
  healer_capacity: 2
  target_main_c: string
  /** Require the selected single-team record to contain this character in any slot. */
  target_character: string
  target_team: string[]
}

export interface OptimizeRequest {
  account: Account
  settings: OptimizeSettings
  database?: Database
}

export interface ResultTeam {
  record_id: string
  main_c: string
  members: TeamMember[]
  rotation_id: string
  difficulty: Difficulty
  dps: number
  weapon_assignment?: Record<string, string> | Array<Record<string, unknown>>
}

export interface Plan {
  feasible: boolean
  total_dps?: number | null
  teams?: ResultTeam[]
  cost?: number | null
  actions?: unknown[]
  gain?: number | null
  gain_percent?: number | null
  roi_per_100_pulls?: number | null
  roi_per_gold?: number | null
  [key: string]: unknown
}

export interface OptimizeResponse {
  current: Plan
  best: Plan
  cost_mode?: CostMode
  pareto_frontier?: Plan[]
  upgrade_rankings?: unknown[]
  exact?: boolean
  explored_combinations?: number
  [key: string]: unknown
}
