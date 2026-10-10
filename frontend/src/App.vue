<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { getCharacterCatalog, getDemoDatabase, getDpsRecognition, getExample, getExampleAccount, getHealth, getPortraitAtlas, getPublicDatabase, getReferenceDps, optimize } from './api'
import { DIFFICULTIES, MODES, type Account, type CharacterCatalog, type CostMode, type Database, type Difficulty, type DpsRecognitionDocument, type LegacyAccount, type OptimizeRequest, type OptimizeResponse, type OptimizeSettings, type Plan, type PortraitAtlas, type ReferenceDps, type ReferenceSection } from './types'
import CharacterSelect from './components/CharacterSelect.vue'
import DpsRecognitionViewer from './components/DpsRecognitionViewer.vue'
import TeamTable from './components/TeamTable.vue'
import { DEFAULT_HEALERS, migrateRepeatableHealers } from './settingsDefaults'

const STORAGE_ACCOUNT = 'wuwa-dps.account.v2'
const STORAGE_ACCOUNT_LEGACY = 'wuwa-dps.account.v1'
const STORAGE_SETTINGS = 'wuwa-dps.settings.v3'
const STORAGE_SETTINGS_PREVIOUS = 'wuwa-dps.settings.v2'
const STORAGE_SETTINGS_LEGACY = 'wuwa-dps.settings.v1'
const STORAGE_BUDGET_PREFERENCES = 'wuwa-dps.budget-preferences.v1'
const FOUR_STAR_CHARACTERS = ['秧秧', '白芷', '炽霞', '丹瑾', '莫特斐', '桃祈', '渊武', '散华', '釉瑚', '灯灯', '秋水', '卜灵'] as const
const FOUR_STAR_SET = new Set<string>(FOUR_STAR_CHARACTERS)

const blankAccount = (): Account => ({ characters: [] })
const defaultSettings = (): OptimizeSettings => ({
  mode: 'single',
  budget: 162.3,
  cost_mode: 'pulls',
  max_difficulty: '中',
  allowed_difficulties: undefined,
  repeatable_healers: [...DEFAULT_HEALERS],
  healer_capacity: 2,
  target_main_c: '',
  target_character: '',
  target_team: [],
})

function readStored<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key)
    return value ? (JSON.parse(value) as T) : fallback
  } catch {
    return fallback
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function parseNewAccount(value: unknown): Account | null {
  if (!isRecord(value) || !Array.isArray(value.characters)) return null
  const candidate: Account = {
    characters: value.characters.map((row) => {
      const item = isRecord(row) ? row : {}
      return {
        character: item.character,
        chain: item.chain,
        signature_refinement: item.signature_refinement,
      }
    }) as Account['characters'],
  }
  return validateAccount(candidate, false) ? null : candidate
}

function migrateAccount(value: unknown): { account: Account; migrated: boolean; warning: string } {
  const hasLegacyWeaponInventory = isRecord(value) && Array.isArray(value.weapons)
  const current = hasLegacyWeaponInventory ? null : parseNewAccount(value)
  if (current) return { account: current, migrated: false, warning: '' }
  if (!isRecord(value) || !Array.isArray(value.characters)) return { account: blankAccount(), migrated: false, warning: '' }
  const legacy = value as unknown as LegacyAccount
  const characters = legacy.characters.map((row) => ({
    character: row?.character,
    chain: row?.chain,
    signature_refinement: Number.isInteger(row?.signature_refinement) ? row.signature_refinement : 0,
  })) as Account['characters']
  const candidate: Account = { characters }
  if (validateAccount(candidate, false)) return { account: blankAccount(), migrated: false, warning: '浏览器中存在格式无效的旧账号资料，未自动载入。' }
  const weaponCount = Array.isArray(legacy.weapons) ? legacy.weapons.length : 0
  return {
    account: candidate,
    migrated: true,
    warning: weaponCount
      ? `已迁移旧账号：${weaponCount} 把旧武器未匹配到角色专武，均按“无专武”处理；请人工核对。`
      : '已迁移旧账号：角色专武均按“无专武”处理；请人工核对。',
  }
}

const savedV2 = readStored<unknown>(STORAGE_ACCOUNT, null)
const loadedAccount = migrateAccount(savedV2 ?? readStored<unknown>(STORAGE_ACCOUNT_LEGACY, null))
const account = reactive<Account>(loadedAccount.account)
const currentSettings = readStored<Partial<OptimizeSettings> | null>(STORAGE_SETTINGS, null)
const savedSettings = currentSettings ?? readStored<Partial<OptimizeSettings> | null>(STORAGE_SETTINGS_PREVIOUS, null)
const legacySettings = readStored<Partial<OptimizeSettings>>(STORAGE_SETTINGS_LEGACY, {})
const savedBudgetPreferences = readStored<Partial<Record<CostMode, number>>>(STORAGE_BUDGET_PREFERENCES, {})
const loadedCostMode: CostMode = savedSettings?.cost_mode === 'gold' ? 'gold' : 'pulls'
const loadedBudget = typeof savedSettings?.budget === 'number'
  ? savedSettings.budget
  : typeof legacySettings.budget === 'number'
    ? legacySettings.budget
    : defaultSettings().budget
const budgetPreferences = reactive<Record<CostMode, number>>({
  pulls: typeof savedBudgetPreferences.pulls === 'number'
    ? savedBudgetPreferences.pulls
    : loadedCostMode === 'pulls' ? loadedBudget : defaultSettings().budget,
  gold: typeof savedBudgetPreferences.gold === 'number'
    ? savedBudgetPreferences.gold
    : loadedCostMode === 'gold' ? loadedBudget : 3,
})
const settings = reactive<OptimizeSettings>({
  ...defaultSettings(),
  ...(savedSettings ?? legacySettings),
  cost_mode: loadedCostMode,
  budget: loadedCostMode === 'gold' ? budgetPreferences.gold : budgetPreferences.pulls,
  repeatable_healers: migrateRepeatableHealers(savedSettings?.repeatable_healers ?? (legacySettings.repeatable_healers?.length ? legacySettings.repeatable_healers : undefined), currentSettings === null),
  healer_capacity: 2,
})
const health = ref<'checking' | 'online' | 'offline'>('checking')
const databaseVersion = ref('尚未加载')
const database = ref<Database | null>(null)
const customDatabaseText = ref('')
const useCustomDatabase = ref(false)
const customDatabaseError = ref('')
const result = ref<OptimizeResponse | null>(null)
const resultSettings = ref<OptimizeSettings | null>(null)
const resultDatabase = ref<Database | null>(null)
const resultSource = ref<'public' | 'custom' | 'demo' | null>(null)
const busy = ref(false)
const loadingExampleAccount = ref(false)
const notice = ref('')
const error = ref('')
const jsonFileInput = ref<HTMLInputElement | null>(null)
const activeTab = ref<'optimizer' | 'reference'>('optimizer')
const assetsExpanded = ref(true)
const catalog = ref<CharacterCatalog | null>(null)
const referenceDps = ref<ReferenceDps | null>(null)
const referenceError = ref('')
const referenceZoom = ref(100)
const referenceViewport = ref<HTMLElement | null>(null)
const referenceHasBeenFitted = ref(false)
const dpsRecognition = ref<DpsRecognitionDocument | null>(null)
const dpsRecognitionError = ref('')
const dpsRecognitionLoading = ref(false)
const portraitAtlas = ref<PortraitAtlas | null>(null)
const portraitAtlasLoaded = ref(false)
const portraitAtlasLoading = ref(false)
const rowKeyCache = new WeakMap<object, string>()
let nextRowKey = 0
let inputRevision = 0

function validateAccount(candidate: Account, requireCatalog: boolean): string | null {
  if (!Array.isArray(candidate.characters)) return '账号 JSON 需要 characters 数组。'
  const names = new Set<string>()
  for (const row of candidate.characters.filter((item) => !item || typeof item.character !== 'string' || !FOUR_STAR_SET.has(item.character.trim()))) {
    if (!row || typeof row.character !== 'string' || !row.character.trim()) return '每个角色都需要名称。'
    if (!Number.isInteger(row.chain) || row.chain < 0 || row.chain > 6) return `角色“${row.character}”的共鸣链必须为 0 至 6。`
    if (!Number.isInteger(row.signature_refinement) || row.signature_refinement < 0 || row.signature_refinement > 5) return `角色“${row.character}”的专武精炼必须为 0 至 5。`
    const character = row.character.trim()
    const canonical = requireCatalog ? resolveCanonicalName(character) : character
    if (requireCatalog && !canonical) return `角色“${character}”不在角色目录中，请从候选列表选择或修正。`
    if (names.has(canonical ?? character)) return `角色“${character}”重复，请只保留一行。`
    names.add(canonical ?? character)
  }
  return null
}

function saveLocal(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Quota or private-browser failures must not interrupt anonymous use.
  }
}

function clearOptimizationResult(): void {
  result.value = null
  resultSettings.value = null
  resultDatabase.value = null
  resultSource.value = null
}

watch(account, () => {
  inputRevision += 1
  saveLocal(STORAGE_ACCOUNT, account)
  clearOptimizationResult()
}, { deep: true })
watch(settings, () => {
  inputRevision += 1
  saveLocal(STORAGE_SETTINGS, settings)
}, { deep: true })

watch(() => settings.cost_mode, (mode) => {
  settings.budget = budgetPreferences[mode]
})

watch(() => settings.budget, (budget) => {
  if (typeof budget === 'number' && Number.isFinite(budget) && budget >= 0) {
    budgetPreferences[settings.cost_mode] = budget
  }
})

watch(budgetPreferences, () => {
  saveLocal(STORAGE_BUDGET_PREFERENCES, budgetPreferences)
}, { deep: true })

const healerText = computed({
  get: () => settings.repeatable_healers.join(', '),
  set: (value: string) => {
    settings.repeatable_healers = value.split(/[,，\n]/).map((name) => name.trim()).filter(Boolean)
  },
})

const targetTeamText = computed({
  get: () => (settings.target_team ?? []).join(', '),
  set: (value: string) => {
    settings.target_team = value.split(/[,，\n]/).map((name) => name.trim()).filter(Boolean)
  },
})

const customDifficulty = computed(() => Array.isArray(settings.allowed_difficulties))
const modeDescription = computed(() => MODES.find(([value]) => value === settings.mode)?.[1] ?? '')
function parseCustomDatabase(): Database | null {
  if (!useCustomDatabase.value || !customDatabaseText.value.trim()) return null
  try {
    const parsed = JSON.parse(customDatabaseText.value) as Database
    if (!Array.isArray(parsed.records) || typeof parsed.version !== 'string') throw new Error('缺少 version 或 records')
    return parsed
  } catch {
    return null
  }
}

function validateCustomDatabase(): void {
  if (!useCustomDatabase.value || !customDatabaseText.value.trim()) {
    customDatabaseError.value = ''
    return
  }
  try {
    const parsed = JSON.parse(customDatabaseText.value) as Database
    if (!Array.isArray(parsed.records) || typeof parsed.version !== 'string') throw new Error('缺少 version 或 records')
    customDatabaseError.value = ''
  } catch (caught) {
    customDatabaseError.value = caught instanceof Error ? `自定义数据库格式错误：${caught.message}` : '自定义数据库格式错误'
  }
}

const customDatabase = computed<Database | null>(() => parseCustomDatabase())
watch([useCustomDatabase, customDatabaseText], () => {
  validateCustomDatabase()
  inputRevision += 1
  clearOptimizationResult()
}, { immediate: true })
const activeDatabaseForResults = computed<Database | null>(() => resultDatabase.value ?? customDatabase.value ?? database.value)
const publicReferenceReady = computed(() => (
  database.value?.version === 'reference-v3.5.2-reviewed'
  || database.value?.metadata?.unit === 'source_numeric'
))
const activeLibraryNotice = computed(() => {
  const activeDatabase = activeDatabaseForResults.value
  const isDemo = resultSource.value === 'demo' || activeDatabase?.version.toLowerCase().startsWith('demo')
  if (isDemo) return '当前使用演示数据库；数值只用于体验优化流程，不代表真实 V3.5.2 DPS。'
  if (activeDatabase?.metadata?.unit === 'source_numeric') return '当前使用真实 V3.5.2 公共参考库（已审阅映射）。'
  if (resultSource.value === 'custom' || (customDatabase.value && !resultSource.value)) {
    return `当前使用本次自定义覆盖库：${activeDatabase?.version ?? '未命名数据库'}。`
  }
  if (activeDatabase) return `当前公共库：${activeDatabase.version}。`
  return '公共参考库正在加载。'
})
const resultUsesSourceNumeric = computed(() => activeDatabaseForResults.value?.metadata?.unit === 'source_numeric')
const resultCostMode = computed<CostMode>(() => result.value?.cost_mode === 'gold' ? 'gold' : 'pulls')
const resultCostLabel = computed(() => resultCostMode.value === 'gold' ? '补金数量' : '期望成本')
const resultRoiLabel = computed(() => resultCostMode.value === 'gold' ? '每金 DPS 收益' : '每 100 抽 DPS 收益')
const positiveUpgradeRankings = computed(() => (result.value?.upgrade_rankings ?? [])
  .filter((plan) => typeof plan.gain === 'number' && plan.gain > 0))
const upgradeSummary = computed(() => {
  const response = result.value
  if (!response?.best.feasible) return null
  const steps = response.upgrade_path ?? []
  const gain = response.current.feasible && typeof response.best.gain === 'number'
    ? response.best.gain : null
  return {
    order: steps.length
      ? steps.map((step) => `第 ${step.gold} 金：${actionText(step.action)}`).join(' → ')
      : '无需补金',
    goldCount: steps.length,
    gain,
    averageGain: gain === null ? null : steps.length ? gain / steps.length : 0,
    gainPercent: response.current.feasible ? response.best.gain_percent : null,
  }
})

const visibleCharacters = computed(() => account.characters.filter((row) => !FOUR_STAR_SET.has(row.character.trim())))
const catalogOptions = computed(() => (catalog.value?.characters ?? [])
  .filter((entry) => !FOUR_STAR_SET.has(entry.character) && !entry.demo_only)
  .map((entry) => ({ value: entry.character, aliases: entry.aliases ?? [] }))
  .sort((left, right) => left.value.localeCompare(right.value, 'zh-CN')))
const targetCatalogOptions = computed(() => (catalog.value?.characters ?? [])
  .filter((entry) => !entry.demo_only)
  .map((entry) => ({ value: entry.character, aliases: entry.aliases ?? [] }))
  .sort((left, right) => left.value.localeCompare(right.value, 'zh-CN')))

const catalogSummary = computed(() => {
  if (!catalog.value) return '正在读取角色目录…'
  const currentUp = catalog.value.current_up?.characters?.length ? `当前 UP：${catalog.value.current_up.characters.join('、')}` : ''
  const asOf = catalog.value.as_of ? `目录截至 ${catalog.value.as_of}` : ''
  return [catalog.value.game_version ? `游戏版本 ${catalog.value.game_version}` : '', asOf, currentUp].filter(Boolean).join(' · ')
})

function addCharacter(): void {
  account.characters.push({ character: '', chain: 0, signature_refinement: 0 })
}

function removeCharacter(row: Account['characters'][number]): void {
  const index = account.characters.indexOf(row)
  if (index >= 0) account.characters.splice(index, 1)
}

function rowKey(row: Account['characters'][number]): string {
  let key = rowKeyCache.get(row)
  if (!key) {
    key = `character-row-${++nextRowKey}`
    rowKeyCache.set(row, key)
  }
  return key
}

function resolveCanonicalName(name: string): string | null {
  const candidate = name.trim()
  if (!candidate) return null
  const matches = (catalog.value?.characters ?? [])
    .filter((entry) => entry.character === candidate || (entry.aliases ?? []).includes(candidate))
    .map((entry) => entry.character)
  return matches.length === 1 ? matches[0] ?? null : null
}

function resolveRequiredTarget(name: string | undefined, label: string): string | null {
  const raw = name?.trim() ?? ''
  if (!raw) {
    error.value = `请为${label}选择一个角色。`
    return null
  }
  const canonical = resolveCanonicalName(raw)
  if (!canonical) {
    error.value = `${label}“${raw}”不在角色目录中，请从候选列表选择。`
    return null
  }
  return canonical
}

function otherSelectedNames(row: Account['characters'][number]): string[] {
  return visibleCharacters.value
    .filter((item) => item !== row)
    .map((item) => resolveCanonicalName(item.character) ?? item.character.trim())
    .filter(Boolean)
}

function selectOptionsForRow(row: Account['characters'][number]): Array<{ value: string; aliases?: string[] }> {
  const matchingDemo = (catalog.value?.characters ?? []).find((entry) => entry.demo_only && resolveCanonicalName(row.character) === entry.character)
  return matchingDemo
    ? [...catalogOptions.value, { value: matchingDemo.character, aliases: matchingDemo.aliases ?? [] }]
    : catalogOptions.value
}

function setCustomDifficulty(enabled: boolean): void {
  settings.allowed_difficulties = enabled ? [settings.max_difficulty] : undefined
}

function onCustomDifficultyToggle(event: Event): void {
  setCustomDifficulty((event.target as HTMLInputElement).checked)
}

function toggleDifficulty(difficulty: Difficulty): void {
  const allowed = new Set(settings.allowed_difficulties ?? [])
  allowed.has(difficulty) ? allowed.delete(difficulty) : allowed.add(difficulty)
  settings.allowed_difficulties = DIFFICULTIES.filter((item) => allowed.has(item))
}

async function loadPublicDatabase(): Promise<void> {
  try {
    database.value = await getPublicDatabase()
    databaseVersion.value = database.value.version
  } catch {
    databaseVersion.value = '后端暂不可用'
  }
}

async function loadCatalog(): Promise<void> {
  try {
    catalog.value = await getCharacterCatalog()
  } catch {
    catalog.value = null
  }
}

async function loadReferenceDps(): Promise<void> {
  referenceError.value = ''
  try {
    referenceDps.value = await getReferenceDps()
    if (activeTab.value === 'reference' && !referenceHasBeenFitted.value) {
      await nextTick()
      fitReference()
      referenceHasBeenFitted.value = true
    }
  } catch (caught) {
    referenceError.value = `原图暂时不可用：${readableError(caught)}`
  }
}

async function loadDpsRecognition(force = false): Promise<void> {
  if (dpsRecognitionLoading.value || (!force && dpsRecognition.value)) return
  dpsRecognitionError.value = ''
  dpsRecognitionLoading.value = true
  try {
    dpsRecognition.value = await getDpsRecognition()
  } catch (caught) {
    dpsRecognitionError.value = `配队校对资料暂时不可用：${readableError(caught)}`
  } finally {
    dpsRecognitionLoading.value = false
  }
}

async function loadPortraitAtlas(): Promise<void> {
  if (portraitAtlasLoaded.value || portraitAtlasLoading.value) return
  portraitAtlasLoading.value = true
  try {
    portraitAtlas.value = await getPortraitAtlas()
  } catch {
    // The draft remains usable without a reference portrait image.
    portraitAtlas.value = null
  } finally {
    portraitAtlasLoaded.value = true
    portraitAtlasLoading.value = false
  }
}

function catalogSignature(character: string): string | null {
  const canonical = resolveCanonicalName(character)
  const found = catalog.value?.characters.find((item) => item.character === canonical)
  return found?.signature_weapon || null
}

function signatureHint(character: string): string {
  if (!character.trim()) return '填写角色名后可显示专武映射。'
  const canonical = resolveCanonicalName(character)
  const entry = catalog.value?.characters.find((item) => item.character === canonical)
  if (entry?.status === 'no_dedicated_signature') return '无独立专武；装备的通用武器不计入此项。'
  const signature = catalogSignature(character)
  return signature ? `已知专武：${signature}` : canonical ? '无独立专武；装备的通用武器不计入此项。' : '当前名称不在角色目录中，请从候选列表修正。'
}

function fitReference(): void {
  const reference = referenceDps.value
  const viewport = referenceViewport.value
  if (!reference || !viewport || reference.width <= 0) return
  referenceZoom.value = Math.max(5, Math.min(200, Math.round(viewport.clientWidth / reference.width * 100)))
}

async function selectTab(tab: 'optimizer' | 'reference'): Promise<void> {
  activeTab.value = tab
  if (tab === 'reference') {
    void loadDpsRecognition()
    void loadPortraitAtlas()
    if (referenceDps.value && !referenceHasBeenFitted.value) {
      await nextTick()
      fitReference()
      referenceHasBeenFitted.value = true
    }
  }
}

function fullResolutionReference(): void {
  referenceZoom.value = 100
}

function jumpToReferenceSection(section: ReferenceSection): void {
  const reference = referenceDps.value
  const viewport = referenceViewport.value
  if (!reference || !viewport) return
  const renderedHeight = reference.height * referenceZoom.value / 100
  const normalized = typeof section.normalized_y === 'number'
    ? section.normalized_y
    : typeof section.y === 'number'
      ? section.y > 1 ? section.y / reference.height : section.y
      : 0
  const target = Math.max(0, Math.min(1, normalized)) * renderedHeight
  viewport.scrollTo({ top: Math.max(0, target - 18), behavior: 'smooth' })
}

async function useExampleAccount(): Promise<void> {
  error.value = ''
  notice.value = ''
  loadingExampleAccount.value = true
  const revisionAtStart = inputRevision
  try {
    const example = parseNewAccount(await getExampleAccount())
    if (!example) throw new Error('示例账号 JSON 格式无效。')
    if (revisionAtStart !== inputRevision) {
      notice.value = '加载期间输入已变更，未替换当前账号资产。'
      return
    }
    account.characters = example.characters
    assetsExpanded.value = true
    notice.value = `已使用示例配置：${example.characters.length} 名角色，${example.characters.filter((row) => row.signature_refinement > 0).length} 把专武。可继续修改资产并点击“开始优化”。`
  } catch (caught) {
    error.value = `载入示例失败：${readableError(caught)}`
  } finally {
    loadingExampleAccount.value = false
  }
}

async function loadExample(): Promise<void> {
  error.value = ''
  notice.value = ''
  const revisionAtStart = inputRevision
  try {
    const example = await getExample()
    const demoDatabase = example.database ?? await getDemoDatabase()
    const demoAccountError = validateAccount(example.account, false)
    if (demoAccountError) throw new Error(`演示账号无效：${demoAccountError}`)
    const demoSettings: OptimizeSettings = {
      ...defaultSettings(),
      ...example.settings,
      cost_mode: 'pulls',
      healer_capacity: 2,
    }
    const payload: OptimizeRequest = JSON.parse(JSON.stringify({
      account: example.account,
      settings: demoSettings,
      database: demoDatabase,
    })) as OptimizeRequest
    busy.value = true
    const response = await optimize(payload)
    if (revisionAtStart !== inputRevision) {
      notice.value = '输入在演示搜索期间已变更，已丢弃演示结果。'
      return
    }
    result.value = response
    resultSettings.value = payload.settings
    resultDatabase.value = demoDatabase
    resultSource.value = 'demo'
    notice.value = '演示优化已完成：使用示例账号、期望抽数预算和演示库；你的账号、偏好与自定义库均未改动。'
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

function chooseJsonImport(): void {
  jsonFileInput.value?.click()
}

async function importAccount(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const raw = JSON.parse(await file.text()) as unknown
    const source = isRecord(raw) && isRecord(raw.account) ? raw.account : raw
    const migrated = migrateAccount(source)
    const validationError = validateAccount(migrated.account, false)
    if (validationError || (!migrated.migrated && migrated.warning)) throw new Error(validationError || migrated.warning)
    account.characters = migrated.account.characters
    notice.value = [migrated.warning, `已导入账号：${file.name}。`].filter(Boolean).join(' ')
  } catch (caught) {
    error.value = `导入失败：${readableError(caught)}`
  }
}

function exportAccount(): void {
  const exported = {
    characters: visibleCharacters.value.map(({ character, chain, signature_refinement }) => ({ character, chain, signature_refinement })),
    assume_four_stars: true,
  }
  const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'wuwa-account.json'
  anchor.click()
  URL.revokeObjectURL(url)
}

async function runOptimize(): Promise<void> {
  error.value = ''
  notice.value = ''
  const accountError = validateAccount(account, true)
  if (accountError) {
    error.value = accountError
    return
  }
  if (useCustomDatabase.value && !customDatabase.value) {
    error.value = customDatabaseError.value || '请修正自定义数据库 JSON。'
    return
  }
  if (!customDatabase.value && !database.value) {
    error.value = '公共参考库尚未加载完成，请稍后重试或使用本次自定义覆盖库。'
    return
  }
  if (!Number.isFinite(settings.budget) || settings.budget < 0) {
    error.value = '补金预算需要是非负数。'
    return
  }
  if (settings.cost_mode === 'gold' && !Number.isInteger(settings.budget)) {
    error.value = '补金数量需要是非负整数。'
    return
  }
  const requestSettings: OptimizeSettings = { ...settings }
  if (requestSettings.mode === 'main_c') {
    const targetMainC = resolveRequiredTarget(requestSettings.target_main_c, '指定主 C')
    if (!targetMainC) return
    requestSettings.target_main_c = targetMainC
  }
  if (requestSettings.mode === 'character') {
    const targetCharacter = resolveRequiredTarget(requestSettings.target_character, '指定角色')
    if (!targetCharacter) return
    requestSettings.target_character = targetCharacter
  }
  if (requestSettings.mode === 'fixed_team') {
    const targets = requestSettings.target_team ?? []
    const normalizedTargets: string[] = []
    for (const target of targets) {
      const canonical = resolveRequiredTarget(target, '固定队伍成员')
      if (!canonical) return
      normalizedTargets.push(canonical)
    }
    requestSettings.target_team = normalizedTargets
  }
  const revisionAtStart = inputRevision
  const databaseAtStart = customDatabase.value ?? database.value
  const sourceAtStart: Exclude<typeof resultSource.value, 'demo' | null> = customDatabase.value ? 'custom' : 'public'
  const requestAccount: Account = {
    characters: visibleCharacters.value.map(({ character, chain, signature_refinement }) => ({
      character: resolveCanonicalName(character) ?? character,
      chain,
      signature_refinement,
    })),
  }
  const payload: OptimizeRequest = JSON.parse(JSON.stringify({
    account: requestAccount,
    settings: requestSettings,
    ...(customDatabase.value ? { database: customDatabase.value } : {}),
  })) as OptimizeRequest
  busy.value = true
  try {
    const response = await optimize(payload)
    if (revisionAtStart !== inputRevision) {
      notice.value = '账号或计算条件在搜索期间已变更，本次响应未应用。请重新计算。'
      return
    }
    result.value = response
    resultSettings.value = payload.settings
    resultDatabase.value = databaseAtStart
    resultSource.value = sourceAtStart
    notice.value = response.exact === false
      ? '结果为近似搜索；查看后端返回的搜索说明。'
      : response.cost_mode === 'gold'
        ? '优化完成。成本按补金数量计算：每个新增角色、链、武器或精炼均为 1 金。'
        : '优化完成。成本为期望抽数，不是保底承诺。'
  } catch (caught) {
    error.value = readableError(caught)
  } finally {
    busy.value = false
  }
}

function clearLocal(): void {
  try {
    localStorage.removeItem(STORAGE_ACCOUNT)
    localStorage.removeItem(STORAGE_ACCOUNT_LEGACY)
    localStorage.removeItem(STORAGE_SETTINGS)
    localStorage.removeItem(STORAGE_SETTINGS_PREVIOUS)
    localStorage.removeItem(STORAGE_SETTINGS_LEGACY)
    localStorage.removeItem(STORAGE_BUDGET_PREFERENCES)
  } catch {
    // Keep in-memory reset available when storage is disabled.
  }
  account.characters = []
  Object.assign(budgetPreferences, { pulls: defaultSettings().budget, gold: 3 })
  Object.assign(settings, defaultSettings())
  clearOptimizationResult()
  notice.value = '已清除当前浏览器保存的匿名账号与偏好。'
}

function readableError(value: unknown): string {
  if (value instanceof Error) return value.message
  return '发生未知错误'
}

function number(value: unknown, digits = 0): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value)
}

function dpsNumber(value: unknown, demoDigits = 0): string {
  return number(value, resultUsesSourceNumeric.value ? Math.max(2, demoDigits) : demoDigits)
}

function signedDps(value: unknown, digits = 0): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${dpsNumber(value, digits)}`
}

function formatPlanCost(value: unknown, costMode = resultCostMode.value): string {
  return `${number(value, costMode === 'gold' ? 0 : 2)} ${costMode === 'gold' ? '金' : '抽'}`
}

function planRoi(plan: Plan, costMode = resultCostMode.value): unknown {
  return costMode === 'gold' ? plan.roi_per_gold : plan.roi_per_100_pulls
}

function percent(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${number(value, 2)}%` : '—'
}

function actionText(action: unknown, costMode = resultCostMode.value): string {
  if (typeof action === 'string') return action
  if (action && typeof action === 'object') {
    const data = action as Record<string, unknown>
    const kind = String(data.kind ?? data.type ?? '')
    const subject = data.character ?? data.weapon ?? data.instance_id ?? ''
    const levels = data.from_chain != null && data.to_chain != null
      ? `C${data.from_chain} → C${data.to_chain}`
      : data.from_refinement != null && data.to_refinement != null
        ? `精${data.from_refinement} → 精${data.to_refinement}`
        : ''
    const names: Record<string, string> = {
      character_acquisition: '获取角色',
      character_chain: '提升共鸣链',
      weapon_acquisition: '获取武器',
      weapon_refinement: '提升武器精炼',
    }
    const cost = typeof data.cost === 'number' ? `（${formatPlanCost(data.cost, costMode)}）` : ''
    const label = [names[kind] ?? kind, subject, levels, cost].filter(Boolean).join(' · ')
    return label || JSON.stringify(action)
  }
  return String(action)
}

function planTeams(plan?: Plan): ReturnType<typeof teamRows> {
  return teamRows(plan)
}

function teamRows(plan?: Plan) {
  return plan?.teams ?? []
}

interface TeamSourceRow {
  recordId: string
  mainC: string
  title?: string
  label?: string
  headers?: string[]
  row?: Array<string | null>
}

function planSourceRows(plan?: Plan): TeamSourceRow[] {
  const records = new Map((activeDatabaseForResults.value?.records ?? []).map((record) => [record.id, record]))
  return (plan?.teams ?? []).flatMap((team) => {
    const source = records.get(team.record_id)?.source
    if (!source) return []
    return [{
      recordId: team.record_id,
      mainC: team.main_c,
      title: source?.title,
      label: source?.label ?? source?.row?.[0] ?? undefined,
      headers: source?.headers,
      row: source?.row,
    }]
  })
}

function sourceRowText(item: TeamSourceRow): string {
  if (!item.headers?.length || !item.row?.length) return ''
  return item.headers
    .map((header, index) => {
      const value = item.row?.[index]
      return value == null || value === '' ? '' : `${header}：${value}`
    })
    .filter(Boolean)
    .join(' · ')
}

function rankingActionText(item: unknown): string {
  const plan = item as Plan
  const action = plan?.actions?.[0]
  return action ? actionText(action) : actionText(item)
}

function rankingStats(item: unknown): string {
  const plan = item as Plan
  if (!plan || typeof plan !== 'object') return ''
  const costMode = resultCostMode.value
  const roiLabel = costMode === 'gold' ? '每金' : '每 100 抽'
  return `${costMode === 'gold' ? '补金数量' : '期望成本'} ${formatPlanCost(plan.cost, costMode)} · DPS ${signedDps(plan.gain, 0)} · ${roiLabel} ${dpsNumber(planRoi(plan, costMode), 1)} DPS`
}

onMounted(async () => {
  saveLocal(STORAGE_SETTINGS, settings)
  if (loadedAccount.migrated) {
    saveLocal(STORAGE_ACCOUNT, account)
    notice.value = loadedAccount.warning
  }
  try {
    await getHealth()
    health.value = 'online'
  } catch {
    health.value = 'offline'
  }
  void loadPublicDatabase()
  void loadCatalog()
  void loadReferenceDps()
})
</script>

<template>
  <main class="page-shell">
    <header class="hero">
      <div>
        <p class="eyebrow">WUTHERING WAVES · PUBLIC BETA</p>
        <h1>Wuwa Pull Planner</h1>
        <p class="hero-subtitle">鸣潮配队与补金规划器</p>
        <p class="hero-copy">基于账号资产、队伍数据库和轴难度，比较当前阵容与可达的最优补金路径。</p>
      </div>
      <div class="status-card" :class="health">
        <span class="status-dot" aria-hidden="true"></span>
        <span>{{ health === 'online' ? '优化服务已连接' : health === 'offline' ? '等待后端服务' : '正在检查服务' }}</span>
        <small>公共库：{{ databaseVersion }}</small>
      </div>
    </header>

    <section class="notice-strip" aria-label="使用说明">
      <span>匿名使用：账号只保存在此浏览器（除本次计算请求外不上传保存）。</span>
      <span>{{ activeLibraryNotice }}</span>
    </section>

    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <div v-if="notice" class="alert success" role="status">{{ notice }}</div>

    <nav class="page-tabs" aria-label="页面内容">
      <button type="button" :class="{ active: activeTab === 'optimizer' }" @click="selectTab('optimizer')">账号与优化</button>
      <button type="button" :class="{ active: activeTab === 'reference' }" @click="selectTab('reference')">全配队 DPS</button>
    </nav>

    <div v-if="activeTab === 'optimizer'" class="workspace">
      <div class="left-column">
        <section class="panel asset-panel">
          <div class="panel-heading">
            <div>
              <p class="section-kicker">01 · 账号资产</p>
              <h2>角色与专武状态</h2>
            </div>
            <div class="heading-actions">
              <button class="button ghost" type="button" :aria-expanded="assetsExpanded" aria-controls="character-assets" @click="assetsExpanded = !assetsExpanded">{{ assetsExpanded ? '收起角色资产 ▴' : '展开角色资产 ▾' }}</button>
              <button class="button secondary" type="button" :disabled="busy || loadingExampleAccount" title="将当前角色与专武资产替换为示例配置" @click="useExampleAccount">{{ loadingExampleAccount ? '正在载入示例…' : '使用示例配置' }}</button>
              <button class="button ghost" type="button" :disabled="busy" @click="loadExample">运行演示优化</button>
              <button class="button ghost" type="button" @click="chooseJsonImport">导入私人账号 JSON</button>
              <button class="button ghost" type="button" @click="exportAccount">导出 JSON</button>
              <input ref="jsonFileInput" class="sr-only" type="file" accept="application/json,.json" @change="importAccount" />
            </div>
          </div>

          <p v-if="!assetsExpanded" class="asset-summary">已录入 {{ visibleCharacters.length }} 名角色 · {{ visibleCharacters.filter((row) => row.signature_refinement > 0).length }} 把专武 · 四星默认 6 链</p>
          <div id="character-assets" v-show="assetsExpanded" class="asset-list">
            <div class="subheading"><div><h3>已拥有角色</h3><p class="muted">每个角色只填写共鸣链与自身专武。奶位跨队重复不会复制同一把专武；第二队仍会按需计算额外武器成本。</p></div><button class="text-button" type="button" @click="addCharacter">+ 添加角色</button></div>
            <p class="four-star-note">四星角色默认已拥有，均 6 链（秧秧、白芷、炽霞、丹瑾、莫特斐、桃祈、渊武、散华、釉瑚、灯灯、秋水、卜灵），无需录入。</p>
            <p class="catalog-note">{{ catalogSummary }}。目录仅用于角色名称校验，不限制补金候选池。</p>
            <div class="form-table role-table">
              <div class="character-row form-header"><span>角色名</span><span>共鸣链</span><span>专武</span><span></span></div>
              <div v-for="row in visibleCharacters" :key="rowKey(row)" class="character-entry">
                <div class="character-row">
                  <CharacterSelect v-model="row.character" :options="selectOptionsForRow(row)" :selected-names="otherSelectedNames(row)" />
                  <select v-model.number="row.chain" aria-label="角色共鸣链">
                    <option v-for="n in 7" :key="n - 1" :value="n - 1">{{ n - 1 }} 链</option>
                  </select>
                  <select v-model.number="row.signature_refinement" aria-label="角色专武精炼">
                    <option :value="0">无</option>
                    <option :value="1">精1</option>
                    <option v-if="[2, 3, 4].includes(row.signature_refinement)" :value="row.signature_refinement">旧存档：精{{ row.signature_refinement }}</option>
                    <option :value="5">精5</option>
                  </select>
                  <button class="icon-button danger" type="button" aria-label="删除角色" @click="removeCharacter(row)">×</button>
                </div>
                <small class="signature-hint">{{ signatureHint(row.character) }}</small>
              </div>
              <p v-if="!visibleCharacters.length" class="empty-inline">尚未录入五星或限定角色。可手工添加、导入私人账号 JSON 或使用示例配置。</p>
            </div>
          </div>
        </section>

        <section class="panel settings-panel">
          <div class="panel-heading"><div><p class="section-kicker">02 · 计算条件</p><h2>目标与约束</h2></div></div>
          <div class="settings-grid">
            <label>优化目标
              <select v-model="settings.mode"><option v-for="[value, label] in MODES" :key="value" :value="value">{{ label }}</option></select>
              <small>{{ modeDescription }}</small>
            </label>
            <label>预算计算方式
              <select v-model="settings.cost_mode"><option value="pulls">期望抽数</option><option value="gold">补金数量</option></select>
              <small>{{ settings.cost_mode === 'gold' ? '角色、链、武器或精炼每新增 1 份均计 1 金。' : '角色链按 81.15 抽、武器精炼按 54.11 抽。' }}</small>
            </label>
            <label>{{ settings.cost_mode === 'gold' ? '补金预算（金）' : '补金预算（期望抽数）' }}<input v-model.number="settings.budget" min="0" :step="settings.cost_mode === 'gold' ? 1 : 0.01" type="number" /><small v-if="settings.cost_mode === 'gold'">仅接受非负整数；切换预算方式会保留两种预算。</small></label>
            <label v-if="settings.mode === 'main_c'">指定主 C
              <CharacterSelect v-model="settings.target_main_c" :options="targetCatalogOptions" :selected-names="[]" />
              <small>仅筛选该角色担任主 C 的单队。</small>
            </label>
            <label v-if="settings.mode === 'character'">指定角色
              <CharacterSelect v-model="settings.target_character" :options="targetCatalogOptions" :selected-names="[]" />
              <small>筛选包含该角色的单队，角色可处于任意槽位。</small>
            </label>
            <label v-if="settings.mode === 'fixed_team'" class="wide">固定队伍成员（逗号分隔）<input v-model="targetTeamText" placeholder="如：今汐, 折枝, 守岸人" /></label>
            <label class="wide">允许跨队重复的奶位（逗号分隔）<input v-model="healerText" placeholder="守岸人, 维里奈, 莫宁, 卜灵, 白芷, 穗穗" /><small>默认名单可自行修改；只有名单中的角色可跨队使用，每名奶位固定最多参与 2 队。</small></label>
          </div>

          <fieldset class="difficulty-box">
            <legend>轴难度筛选</legend>
            <label class="switch-row"><input :checked="customDifficulty" type="checkbox" @change="onCustomDifficultyToggle" /> 自定义难度集合</label>
            <template v-if="!customDifficulty">
              <label>最高允许难度<select v-model="settings.max_difficulty"><option v-for="difficulty in DIFFICULTIES" :key="difficulty" :value="difficulty">≤ {{ difficulty }}</option></select></label>
              <p>系统会在不高于此难度的合法轴中取代表 DPS。</p>
            </template>
            <template v-else>
              <div class="difficulty-options"><label v-for="difficulty in DIFFICULTIES" :key="difficulty"><input :checked="settings.allowed_difficulties?.includes(difficulty)" type="checkbox" @change="toggleDifficulty(difficulty)" /> {{ difficulty }}</label></div>
              <p>每个队伍会在所选难度的合法轴中取最高 DPS。</p>
            </template>
          </fieldset>
          <div class="settings-run">
            <button class="button primary full" type="button" :disabled="busy" @click="runOptimize">{{ busy ? '正在搜索路径…' : '开始优化' }}</button>
            <p class="run-note" v-if="settings.cost_mode === 'gold'">补金模式：UP 角色、每条共鸣链、UP 武器与每次精炼均按 1 金计算；会自动包含前置升级，不使用抽卡期望。</p>
            <p class="run-note" v-else>角色链按 81.15 抽、武器精炼按 54.11 抽计算期望成本。结果不是抽卡结果保证。</p>
          </div>
        </section>

        <section class="panel database-panel">
          <div class="panel-heading"><div><p class="section-kicker">可选 · 本地覆盖</p><h2>自定义配队数据库</h2></div><label class="switch-row"><input v-model="useCustomDatabase" type="checkbox" /> 此次请求使用</label></div>
          <p class="muted">不会修改公共数据库。用于临时试验、补录或导入自己的队伍记录。</p>
          <textarea v-model="customDatabaseText" :disabled="!useCustomDatabase" spellcheck="false" placeholder="粘贴数据库 JSON；需要 version 与 records。"></textarea>
          <p v-if="customDatabaseError" class="field-error">{{ customDatabaseError }}</p>
        </section>
      </div>

      <aside class="right-column">
        <section class="panel run-panel">
          <p class="section-kicker">本次计算</p>
          <h2>条件与资产概览</h2>
          <dl class="run-summary"><div><dt>模式</dt><dd>{{ modeDescription }}</dd></div><div><dt>预算</dt><dd>{{ settings.cost_mode === 'gold' ? `${number(settings.budget, 0)} 金` : `${number(settings.budget, 2)} 抽` }}</dd></div><div><dt>已录角色</dt><dd>{{ account.characters.length }} 名</dd></div><div><dt>已录专武</dt><dd>{{ account.characters.filter((row) => row.signature_refinement > 0).length }} 把</dd></div></dl>
          <p class="run-note"><strong>简化资产模式：</strong>表内常驻武器按每队精1可用；专武按已录入值计算。漂泊者形态需手动录入对应形态和链数，不能通过抽卡补足。</p>
          <button class="text-button reset" type="button" @click="clearLocal">清除本机账号与偏好</button>
        </section>

        <section v-if="result" class="panel result-panel">
          <div class="panel-heading"><div><p class="section-kicker">计算结果</p><h2>当前与推荐方案</h2></div><span class="exact-badge" :class="result.exact === false ? 'approximate' : ''">{{ result.exact === false ? '近似搜索' : '精确搜索' }}</span></div>
          <p v-if="resultSettings" class="muted">本次结果条件：{{ MODES.find(([mode]) => mode === resultSettings?.mode)?.[1] }} · 预算 {{ formatPlanCost(resultSettings.budget, resultSettings.cost_mode) }} · 难度 {{ resultSettings.allowed_difficulties?.join(' / ') ?? `≤ ${resultSettings.max_difficulty}` }}</p>
          <div class="score-grid">
            <article><span>当前总 DPS</span><strong>{{ result.current.feasible ? dpsNumber(result.current.total_dps, 0) : '不可成立' }}</strong></article>
            <article class="featured"><span>最佳总 DPS</span><strong>{{ result.best.feasible ? dpsNumber(result.best.total_dps, 0) : '预算内无解' }}</strong></article>
          </div>
          <p v-if="resultSource === 'demo'" class="muted">演示结果：使用示例账号、示例预算与演示数据库，不影响你的账号或偏好。</p>
          <p v-if="!result.current.feasible || !result.best.feasible" class="infeasible">当前资产或预算无法组成所选模式所需的完整队伍。请检查奶位白名单、武器库存、难度与预算。</p>
          <div v-if="result.best.feasible" class="metrics"><div><span>{{ resultCostLabel }}</span><b>{{ formatPlanCost(result.best.cost) }}</b></div><div><span>DPS 提升</span><b>{{ signedDps(result.best.gain, 0) }}</b></div><div><span>提升比例</span><b>{{ percent(result.best.gain_percent) }}</b></div><div><span>{{ resultRoiLabel }}</span><b>{{ dpsNumber(planRoi(result.best), 1) }}</b></div></div>
          <p v-if="typeof result.explored_combinations === 'number'" class="muted">已探索 {{ number(result.explored_combinations) }} 个组合状态。</p>
        </section>
      </aside>
    </div>

    <section v-if="activeTab === 'optimizer' && result" class="results-area">
      <section class="panel result-detail">
        <div class="panel-heading"><div><p class="section-kicker">当前阵容</p><h2>当前可成立队伍</h2></div></div>
        <TeamTable :teams="planTeams(result.current)" />
        <div v-if="planSourceRows(result.current).length" class="action-list">
          <h3>原图映射出处</h3>
          <ul class="rankings">
            <li v-for="item in planSourceRows(result.current)" :key="item.recordId"><strong>{{ item.title ?? item.recordId }}</strong><span>配置：{{ item.label ?? '未提供配置标签' }}<template v-if="sourceRowText(item)"> · {{ sourceRowText(item) }}</template></span></li>
          </ul>
        </div>
      </section>
      <section class="panel result-detail">
        <div class="panel-heading"><div><p class="section-kicker">推荐路径</p><h2>逐金补金顺序与收益</h2></div></div>
        <div v-if="upgradeSummary" class="upgrade-summary">
          <h3>补金汇总</h3>
          <p class="upgrade-summary-order"><strong>总补金顺序：</strong>{{ upgradeSummary.order }}</p>
          <div class="metrics">
            <div><span>总 DPS 提升</span><b>{{ signedDps(upgradeSummary.gain) }}</b></div>
            <div><span>平均每金 DPS 提升</span><b>{{ signedDps(upgradeSummary.averageGain, 2) }}</b></div>
            <div><span>总提升率</span><b>{{ percent(upgradeSummary.gainPercent) }}</b></div>
            <div><span>实际补金数量</span><b>{{ upgradeSummary.goldCount }} 金</b></div>
          </div>
          <p class="muted">平均每金 DPS 提升 = 总 DPS 提升 ÷ 实际补金数量；总提升率相对补金前的最优总 DPS。<template v-if="!result.current.feasible">补金前完整队伍不可成立，收益比较显示为 —。</template></p>
        </div>
        <template v-if="result.upgrade_path?.length">
          <p class="muted">优先保证目标预算内最终 DPS 最优，再依次最大化每金后的 DPS。提升率相较上一金后的最优总 DPS；零提升步骤是后续升级的前置投入。</p>
          <div class="scroll-table">
            <table class="upgrade-path-table">
              <thead><tr><th>补金顺序</th><th>补在哪里</th><th>补完后总 DPS</th><th>该金 DPS 提升</th><th>该金提升率</th><th>累计投入</th><th>补完后的最优队伍 / 每队 DPS</th></tr></thead>
              <tbody>
                <tr v-for="step in result.upgrade_path" :key="step.gold">
                  <td>第 {{ step.gold }} 金</td>
                  <td>{{ actionText(step.action) }}</td>
                  <td>{{ step.feasible ? dpsNumber(step.total_dps) : '完整队伍尚不可成立' }}</td>
                  <td>{{ signedDps(step.gain) }}</td>
                  <td>{{ percent(step.gain_percent) }}</td>
                  <td>{{ formatPlanCost(step.cumulative_cost) }}</td>
                  <td><div v-for="(team, index) in step.teams" :key="index">队伍 {{ index + 1 }}：{{ team.members.map((member) => member.character).join(' + ') }} · DPS {{ dpsNumber(team.dps) }}</div><span v-if="!step.feasible">—</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="!result.current.feasible" class="muted">完整队伍首次成立前没有可比较的总 DPS 基准，提升率显示为 —。</p>
        </template>
        <p v-else-if="result.best.feasible" class="muted">当前资产已达到本次预算内的最优 DPS，无需补金。</p>
        <h3 class="final-team-heading">目标预算内补金后的最优队伍</h3>
        <p v-if="result.best.feasible" class="muted">共补 {{ result.upgrade_path?.length ?? 0 }} 金，投入 {{ formatPlanCost(result.best.cost) }}，最优总 DPS {{ dpsNumber(result.best.total_dps) }}。</p>
        <TeamTable :teams="planTeams(result.best)" />
        <div v-if="planSourceRows(result.best).length" class="action-list">
          <h3>原图映射出处</h3>
          <ul class="rankings">
            <li v-for="item in planSourceRows(result.best)" :key="item.recordId"><strong>{{ item.title ?? item.recordId }}</strong><span>配置：{{ item.label ?? '未提供配置标签' }}<template v-if="sourceRowText(item)"> · {{ sourceRowText(item) }}</template></span></li>
          </ul>
        </div>
      </section>
      <section v-if="positiveUpgradeRankings.length" class="panel result-detail">
        <div class="panel-heading"><div><p class="section-kicker">候选分析</p><h2>升级收益排行</h2></div></div>
        <ol class="rankings"><li v-for="(item, index) in positiveUpgradeRankings" :key="index"><strong>{{ rankingActionText(item) }}</strong><span>{{ rankingStats(item) }}</span></li></ol>
      </section>
      <details class="panel raw-result"><summary>查看原始优化响应（调试）</summary><pre>{{ JSON.stringify(result, null, 2) }}</pre></details>
    </section>

    <section v-if="activeTab === 'reference'" class="reference-page">
      <section class="panel reference-intro">
        <div>
          <p class="section-kicker">完整原图参考</p>
          <h2>全配队金数 DPS</h2>
          <p class="muted">这里完整展示原始 DPS 图片，包括作者注记、图例和全部数值。<template v-if="publicReferenceReady">默认优化器已接入已审阅的结构化映射；覆盖范围和未映射来源项会在下方资料卡中说明，未映射来源仍只供查阅。</template><template v-else-if="database">当前公共库尚未提供已审阅的原图映射；未映射来源只供查阅。</template><template v-else>公共结构化参考库正在加载；未映射来源只供查阅。</template></p>
        </div>
        <span v-if="referenceDps" class="exact-badge">{{ referenceDps.version }}</span>
      </section>

      <section v-if="referenceDps" class="panel reference-viewer">
        <div class="reference-toolbar">
          <div class="zoom-controls">
            <label>缩放 <input v-model.number="referenceZoom" type="range" min="5" max="200" step="1" /> {{ referenceZoom }}%</label>
            <button class="button ghost" type="button" @click="fitReference">适合宽度</button>
            <button class="button ghost" type="button" @click="fullResolutionReference">原始分辨率</button>
          </div>
          <div v-if="referenceDps.sections?.length" class="reference-sections" aria-label="跳转图片分区">
            <span>跳转：</span>
            <button v-for="section in referenceDps.sections" :key="section.id" class="text-button" type="button" @click="jumpToReferenceSection(section)">{{ section.label }}</button>
          </div>
        </div>
        <div ref="referenceViewport" class="reference-image-viewport">
          <img :src="referenceDps.image_url" :alt="`全配队金数 DPS 原图（${referenceDps.version}）`" :style="{ width: `${referenceDps.width * referenceZoom / 100}px` }" />
        </div>
      </section>
      <section v-else class="panel reference-unavailable">
        <p>{{ referenceError || '正在读取全配队 DPS 原图…' }}</p>
        <button v-if="referenceError" class="button ghost" type="button" @click="loadReferenceDps">重试</button>
      </section>

      <DpsRecognitionViewer
        v-if="dpsRecognition"
        :document="dpsRecognition"
        :source-image-url="referenceDps?.image_url ?? '/api/reference-dps/image'"
        :portraits="portraitAtlas?.portraits ?? []"
        :coverage="database?.metadata ?? null"
      />
      <section v-else class="panel recognition-unavailable" aria-live="polite">
        <p v-if="dpsRecognitionLoading">正在加载配队校对资料…</p>
        <template v-else-if="dpsRecognitionError">
          <p>{{ dpsRecognitionError }}</p>
          <button class="button ghost" type="button" @click="loadDpsRecognition(true)">重试加载资料</button>
        </template>
        <p v-else>打开此页后加载公共配队资料；本地校对与账号资产分别保存。</p>
      </section>
    </section>
  </main>
</template>
