<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CSSProperties } from 'vue'
import type { DatabaseMetadata, DpsRecognitionCharacter, DpsRecognitionDocument, DpsRecognitionPanel, PortraitAtlasEntry } from '../types'
import CharacterSelect from './CharacterSelect.vue'

const props = defineProps<{
  document: DpsRecognitionDocument
  sourceImageUrl: string
  portraits: PortraitAtlasEntry[]
  coverage?: DatabaseMetadata | null
}>()

const selectedPanelId = ref('')
const selectedTableIndex = ref(-1)
const showReadingOrder = ref(false)
const reviewFilter = ref<'all' | 'pending' | 'corrected'>('all')
const localCorrections = ref<Record<string, Record<string, string>>>({})
const draftCorrections = ref<Record<string, string>>({})
const reviewMessage = ref('')
const REVIEW_STORAGE_KEY = 'wuwa-dps.dps-recognition.local-review.v1'
const ROVER_FORMS = [
  { value: '漂泊者·衍射', aliases: ['光主', '衍射漂泊者'] },
  { value: '漂泊者·湮灭', aliases: ['暗主', '湮灭漂泊者'] },
  { value: '漂泊者·导电', aliases: ['雷主', '导电漂泊者'] },
]

interface StoredLocalReview {
  schema_version: 'dps-recognition-local-review-1'
  source_sha256: string
  corrections: Record<string, Record<string, string>>
}

watch(selectedPanelId, () => { selectedTableIndex.value = -1 })

watch(() => props.document.panels, (panels) => {
  if (!panels.some((panel) => panel.id === selectedPanelId.value)) {
    selectedPanelId.value = panels[0]?.id ?? ''
  }
}, { immediate: true })

const selectedPanel = computed<DpsRecognitionPanel | undefined>(() => (
  props.document.panels.find((panel) => panel.id === selectedPanelId.value)
))

const sourceHash = computed(() => props.document.source.sha256?.trim() ?? '')
const portraitOptions = computed(() => [...props.portraits
  .map((portrait) => ({ value: portrait.canonical, aliases: portrait.aliases ?? [] })), ...ROVER_FORMS]
  .sort((left, right) => left.value.localeCompare(right.value, 'zh-CN')))

function normalizedName(value: string): string {
  return value.trim().normalize('NFKC').replace(/[\s·・]/g, '').toLocaleLowerCase('zh-CN')
}

function canonicalPortraitName(value: string | null | undefined): string | null {
  if (!value) return null
  const normalized = normalizedName(value)
  const rover = ROVER_FORMS.find((form) => normalizedName(form.value) === normalized
    || form.aliases.some((alias) => normalizedName(alias) === normalized))
  if (rover) return rover.value
  const direct = props.portraits.find((portrait) => normalizedName(portrait.canonical) === normalized)
  const alias = direct ?? props.portraits.find((portrait) => (portrait.aliases ?? []).some((name) => normalizedName(name) === normalized))
  return alias?.canonical ?? null
}

function correctionKey(panelId: string, index: number): string {
  return `${panelId}:${index}`
}

function localCorrection(panelId: string, index: number): string | null {
  return canonicalPortraitName(localCorrections.value[panelId]?.[String(index)])
}

function hasLocalCorrection(panelId: string, index: number): boolean {
  return localCorrection(panelId, index) !== null
}

function publicConfirmedName(character: DpsRecognitionCharacter): string | null {
  return character.identity_verification && character.name ? character.name : null
}

function reviewExportName(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): string | null {
  // Do not promote automatic candidates into the review export.
  return localCorrection(panel.id, index) ?? publicConfirmedName(character)
}

function slotNeedsReview(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): boolean {
  return !hasLocalCorrection(panel.id, index) && !publicConfirmedName(character)
}

function hasPanelCorrection(panel: DpsRecognitionPanel): boolean {
  return panel.characters.some((_character, index) => hasLocalCorrection(panel.id, index))
}

const filteredPanels = computed(() => props.document.panels.filter((panel) => {
  if (reviewFilter.value === 'pending') return panel.characters.some((character, index) => slotNeedsReview(panel, character, index))
  if (reviewFilter.value === 'corrected') return hasPanelCorrection(panel)
  return true
}))

const panelsBySection = computed(() => {
  const groups = new Map<string, DpsRecognitionPanel[]>()
  for (const panel of filteredPanels.value) {
    const section = panel.section_id || '未归类分区'
    const existing = groups.get(section) ?? []
    existing.push(panel)
    groups.set(section, existing)
  }
  return [...groups.entries()].map(([section, panels]) => ({ section, panels }))
})

const selectedIndex = computed(() => filteredPanels.value.findIndex((panel) => panel.id === selectedPanelId.value))
const selectedNotes = computed(() => selectedPanel.value?.notes?.map((note) => note.text).filter(Boolean) ?? [])
const readingOrderText = computed(() => props.document.raw_ocr?.reading_order_text?.trim() ?? '')
const reviewedTables = computed(() => selectedPanel.value?.manual_review?.tables ?? [])
const unitNote = computed(() => props.document.manual_review?.unit_note?.trim() || '保留原图读数，单位未明示，未乘以 10000。')
const mappedPanelIds = computed(() => new Set((props.coverage?.mapped_table_ids ?? []).map(id => id.split('/')[0])))
const selectedMappingStatus = computed(() => selectedPanel.value && mappedPanelIds.value.has(selectedPanel.value.id)
  ? '含已接入的配置；排除项不参与计算' : '仅供资料查阅')
const coverageSummary = computed(() => {
  const coverage = props.coverage
  if (!coverage) return ''
  const tables = typeof coverage.mapped_table_count === 'number' && typeof coverage.source_table_count === 'number'
    ? `已映射 ${coverage.mapped_table_count}/${coverage.source_table_count} 张来源表`
    : ''
  const rows = typeof coverage.mapped_row_count === 'number' && typeof coverage.source_row_count === 'number'
    ? `${coverage.mapped_row_count}/${coverage.source_row_count} 行`
    : ''
  const excluded = Array.isArray(coverage.excluded_tables) && coverage.excluded_tables.length
    ? `另有 ${coverage.excluded_tables.length} 项未映射来源（可能为整表或单行）仅供查阅`
    : ''
  return [tables, rows, excluded].filter(Boolean).join('；')
})
const coverageRuleSummary = computed(() => {
  const rules = props.coverage?.rules
  const messages: string[] = []
  if (rules?.cumulative_upgrades) messages.push('同表高配行按累计配置解释')
  if (rules?.unspecified_signature_refinement === 1) messages.push('未标精炼的“专”按精1')
  return messages.join('；')
})
const selectedRows = computed(() => selectedTableIndex.value < 0
  ? selectedPanel.value?.table_rows ?? []
  : selectedPanel.value?.tables?.[selectedTableIndex.value]?.table_rows ?? [])

function loadLocalReview(): void {
  localCorrections.value = {}
  draftCorrections.value = {}
  reviewMessage.value = ''
  if (!sourceHash.value) {
    reviewMessage.value = '当前识别来源未提供 SHA-256，无法绑定本地修正。'
    return
  }
  try {
    const raw = localStorage.getItem(REVIEW_STORAGE_KEY)
    if (!raw) return
    const stored = JSON.parse(raw) as Partial<StoredLocalReview>
    if (stored.source_sha256 !== sourceHash.value || !stored.corrections || typeof stored.corrections !== 'object') {
      reviewMessage.value = '检测到其他来源图片的本地修正，未应用到当前原图。'
      return
    }
    localCorrections.value = stored.corrections
    reviewMessage.value = '已载入与当前源图 SHA-256 匹配的本地修正。'
  } catch {
    reviewMessage.value = '无法读取浏览器中的本地修正。'
  }
}

function persistLocalReview(): void {
  if (!sourceHash.value) return
  const value: StoredLocalReview = {
    schema_version: 'dps-recognition-local-review-1',
    source_sha256: sourceHash.value,
    corrections: localCorrections.value,
  }
  try {
    localStorage.setItem(REVIEW_STORAGE_KEY, JSON.stringify(value))
  } catch {
    reviewMessage.value = '浏览器无法保存本地修正；本次页面仍可继续核对。'
  }
}

watch(sourceHash, loadLocalReview, { immediate: true })
watch(filteredPanels, (panels) => {
  if (!panels.some((panel) => panel.id === selectedPanelId.value)) {
    selectedPanelId.value = panels[0]?.id ?? ''
  }
}, { immediate: true })

function draftCorrection(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): string {
  const key = correctionKey(panel.id, index)
  if (Object.prototype.hasOwnProperty.call(draftCorrections.value, key)) return draftCorrections.value[key] ?? ''
  return localCorrection(panel.id, index) ?? publicConfirmedName(character) ?? ''
}

function setDraftCorrection(panelId: string, index: number, value: string): void {
  draftCorrections.value = { ...draftCorrections.value, [correctionKey(panelId, index)]: value }
}

function canSaveCorrection(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): boolean {
  const proposed = canonicalPortraitName(draftCorrection(panel, character, index))
  return Boolean(proposed && proposed !== localCorrection(panel.id, index))
}

function saveCorrection(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): void {
  const proposed = canonicalPortraitName(draftCorrection(panel, character, index))
  if (!proposed) {
    reviewMessage.value = '请从目录头像中选择角色后再保存；不能自由输入名称。'
    return
  }
  localCorrections.value = {
    ...localCorrections.value,
    [panel.id]: { ...(localCorrections.value[panel.id] ?? {}), [String(index)]: proposed },
  }
  persistLocalReview()
  reviewMessage.value = `已保存 ${panel.id} 的槽位 ${index + 1} 本地修正。`
}

function revokeCorrection(panel: DpsRecognitionPanel, index: number): void {
  if (!hasLocalCorrection(panel.id, index)) return
  const panelCorrections = { ...(localCorrections.value[panel.id] ?? {}) }
  delete panelCorrections[String(index)]
  const next = { ...localCorrections.value }
  if (Object.keys(panelCorrections).length) next[panel.id] = panelCorrections
  else delete next[panel.id]
  localCorrections.value = next
  const key = correctionKey(panel.id, index)
  const nextDrafts = { ...draftCorrections.value }
  delete nextDrafts[key]
  draftCorrections.value = nextDrafts
  persistLocalReview()
  reviewMessage.value = `已撤销 ${panel.id} 的槽位 ${index + 1} 本地修正。`
}

function exportLocalReview(): void {
  if (!sourceHash.value) {
    reviewMessage.value = '当前识别来源缺少 SHA-256，无法导出可合并的核对文件。'
    return
  }
  if (!portraitOptions.value.length) {
    reviewMessage.value = '目录头像仍在加载，暂不能导出经过目录校验的核对文件。'
    return
  }
  const payload = {
    schema_version: 'dps-recognition-review-1',
    source_sha256: sourceHash.value,
    panels: props.document.panels.map((panel) => ({
      id: panel.id,
      characters: panel.characters.map((character, index) => reviewExportName(panel, character, index)),
    })),
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `wuwa-dps-review-${sourceHash.value.slice(0, 12)}.json`
  anchor.click()
  URL.revokeObjectURL(url)
  reviewMessage.value = '已导出源图 SHA-256 绑定的 review JSON，供离线合并。'
}

function movePanel(direction: -1 | 1): void {
  const index = filteredPanels.value.findIndex((panel) => panel.id === selectedPanelId.value)
  const next = filteredPanels.value[index + direction]
  if (next) selectedPanelId.value = next.id
}

function onReadingOrderToggle(event: Event): void {
  showReadingOrder.value = (event.target as HTMLDetailsElement).open
}

function panelLabel(panel: DpsRecognitionPanel): string {
  const roles = panel.characters.map((character, index) => localCorrection(panel.id, index)
    ?? character.name ?? character.candidates?.[0]?.name ?? '待核').join(' / ')
  const difficulty = panel.difficulty ? ` · ${panel.difficulty}` : ''
  return `${roles || '未识别角色'}${difficulty}`
}

function roleKind(character: DpsRecognitionCharacter): 'manual' | 'automatic' | 'review' {
  if (character.identity_verification) return 'manual'
  return character.name && !character.unknown && !character.automatic_unknown ? 'automatic' : 'review'
}

function roleHeading(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): string {
  if (hasLocalCorrection(panel.id, index)) return '本地修正'
  const kind = roleKind(character)
  if (kind === 'manual') return '公共人工核对'
  if (kind === 'automatic') return '自动候选'
  return '待核候选'
}

function similarity(score: number | null | undefined): string {
  return typeof score === 'number' && Number.isFinite(score) ? score.toFixed(3) : '—'
}

function cropStyle(panel: DpsRecognitionPanel): CSSProperties {
  const rect = panel.source_rect
  if (!rect || rect.width <= 0 || rect.height <= 0 || !props.sourceImageUrl) return {}
  const displayWidth = Math.min(340, Math.max(220, rect.width * 0.58))
  const scale = displayWidth / rect.width
  return {
    width: `${displayWidth}px`,
    aspectRatio: `${rect.width} / ${rect.height}`,
    backgroundImage: `url("${props.sourceImageUrl}")`,
    backgroundSize: `${props.document.source.width * scale}px ${props.document.source.height * scale}px`,
    backgroundPosition: `-${rect.x * scale}px -${rect.y * scale}px`,
  }
}

function displayedReferenceName(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): string | null {
  const local = localCorrection(panel.id, index)
  if (local) return local
  if (roleKind(character) === 'review') return character.candidates?.[0]?.name ?? null
  // A manually verified name takes priority over its old automatic candidate ID.
  return character.name ?? null
}

function referencePortrait(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): PortraitAtlasEntry | undefined {
  const name = displayedReferenceName(panel, character, index)
  if (!name) return undefined
  const normalized = normalizedName(name)
  return props.portraits.find((portrait) => normalizedName(portrait.canonical) === normalized)
    ?? props.portraits.find((portrait) => (portrait.aliases ?? []).some((alias) => normalizedName(alias) === normalized))
    ?? (name.startsWith('漂泊者·') ? props.portraits.find((portrait) => portrait.canonical === '漂泊者·气动') : undefined)
}

function referenceLabel(panel: DpsRecognitionPanel, character: DpsRecognitionCharacter, index: number): string {
  if (displayedReferenceName(panel, character, index)?.startsWith('漂泊者·')) return '同外观参考（形态依原文）'
  if (hasLocalCorrection(panel.id, index)) return '本地修正参考'
  return roleKind(character) === 'review' ? '候选参考' : '目录参考'
}

function avatarCropStyle(character: DpsRecognitionCharacter): CSSProperties {
  const rect = character.source_rect
  if (!rect || rect.width <= 0 || rect.height <= 0 || !props.sourceImageUrl) return {}
  const displaySize = 64
  const scale = displaySize / rect.width
  return {
    width: `${displaySize}px`,
    height: `${displaySize}px`,
    backgroundImage: `url("${props.sourceImageUrl}")`,
    backgroundSize: `${props.document.source.width * scale}px ${props.document.source.height * scale}px`,
    backgroundPosition: `-${rect.x * scale}px -${rect.y * scale}px`,
  }
}
</script>

<template>
  <section class="panel recognition-viewer" aria-labelledby="recognition-heading">
    <div class="panel-heading recognition-heading">
      <div>
        <p class="section-kicker">原图资料 · 本地校对</p>
        <h2 id="recognition-heading">DPS 图识别记录</h2>
        <p class="muted">头像与已转录表格可逐卡对照原图。公共库只采用已审核映射；未映射内容仍只供资料查阅。本地校正单独保存，不会改动你的账号资产。</p>
      </div>
      <span class="exact-badge approximate">资料校对 · 只读</span>
    </div>
    <p v-if="coverageSummary" class="muted recognition-coverage">公共优化库：{{ coverageSummary }}。<template v-if="coverageRuleSummary">{{ coverageRuleSummary }}。</template> 未映射来源不会参与优化。</p>
    <p v-else class="muted recognition-coverage">当前资料页展示原图与转录草稿；只有公共库中已映射的来源表会参与优化。</p>

    <dl class="recognition-summary" aria-label="识别草稿统计">
      <div><dt>卡片</dt><dd>{{ document.summary.panel_count ?? document.panels.length }} 张</dd></div>
      <div><dt>表区</dt><dd>{{ document.summary.table_count ?? document.panels.reduce((total, panel) => total + (panel.tables?.length ?? 0), 0) }} 个</dd></div>
      <div><dt>原文块</dt><dd>{{ document.summary.ocr_token_count?.toLocaleString('zh-CN') ?? '—' }}</dd></div>
      <div><dt>头像槽</dt><dd>{{ document.summary.avatar_slots ?? '—' }}</dd></div>
      <div><dt>待核头像</dt><dd>{{ document.summary.unknown_avatar_slots ?? '—' }}</dd></div>
      <div><dt>已转录表格</dt><dd>{{ document.summary.visually_transcribed_table_count ?? 0 }} 张</dd></div>
      <div><dt>含已映射配置</dt><dd>{{ mappedPanelIds.size }} 张</dd></div>
    </dl>

    <div class="recognition-review-controls">
      <label class="recognition-picker">审查筛选
        <select v-model="reviewFilter" aria-label="筛选 DPS 识别卡片">
          <option value="all">全部</option>
          <option value="pending">头像尚待核对</option>
          <option value="corrected">本地已修正</option>
        </select>
      </label>
      <label class="recognition-picker">选择卡片
        <select v-model="selectedPanelId" aria-label="选择一张 DPS 识别卡片" :disabled="!filteredPanels.length">
          <optgroup v-for="group in panelsBySection" :key="group.section" :label="group.section">
            <option v-for="panel in group.panels" :key="panel.id" :value="panel.id">{{ panel.id }} · {{ panelLabel(panel) }}</option>
          </optgroup>
        </select>
      </label>
      <div class="review-control-actions">
        <button class="button ghost" type="button" :disabled="selectedIndex <= 0" @click="movePanel(-1)">上一张</button>
        <button class="button ghost" type="button" :disabled="selectedIndex < 0 || selectedIndex >= filteredPanels.length - 1" @click="movePanel(1)">下一张</button>
        <button class="button secondary" type="button" @click="exportLocalReview">导出校对记录</button>
      </div>
    </div>
    <p v-if="reviewMessage" class="muted" role="status">{{ reviewMessage }}</p>

    <template v-if="selectedPanel">
      <div class="recognition-panel-meta">
        <span>卡片 {{ selectedIndex + 1 }} / {{ filteredPanels.length }}（筛选结果）</span>
        <span v-if="selectedPanel.difficulty">难度原文：{{ selectedPanel.difficulty }}</span>
        <span v-if="selectedPanel.stability">稳定性原文：{{ selectedPanel.stability }}</span>
        <span>状态：{{ selectedMappingStatus }}</span>
      </div>

      <div class="recognition-detail-grid">
        <section class="recognition-roles" aria-label="角色头像匹配结果">
          <h3>角色槽位</h3>
          <article v-for="(character, index) in selectedPanel.characters" :key="index" class="recognition-role" :class="[roleKind(character), { 'local-correction': hasLocalCorrection(selectedPanel.id, index) }]">
            <span class="recognition-role-index">{{ index + 1 }}</span>
            <div>
              <p><b>{{ roleHeading(selectedPanel, character, index) }}</b></p>
              <template v-if="hasLocalCorrection(selectedPanel.id, index)">
                <strong>{{ localCorrection(selectedPanel.id, index) }}</strong>
                <small>此名称保存在当前浏览器，未写入公共草稿。</small>
              </template>
              <template v-else-if="roleKind(character) === 'review'">
                <strong>{{ character.candidates?.[0]?.name ?? '无可用候选' }}</strong>
                <small>候选 1 · 相似度 {{ similarity(character.candidates?.[0]?.score) }}</small>
                <template v-if="character.candidates?.[1]">
                  <strong>{{ character.candidates[1].name }}</strong>
                  <small>候选 2 · 相似度 {{ similarity(character.candidates[1].score) }}</small>
                </template>
              </template>
              <template v-else>
                <strong>{{ character.name ?? character.candidates?.[0]?.name ?? '未标注' }}</strong>
                <small>自动匹配相似度 {{ similarity(character.score ?? character.candidates?.[0]?.score) }}<template v-if="roleKind(character) === 'manual'"> · 已记录人工核对标记</template></small>
              </template>
              <div v-if="character.source_rect" class="avatar-comparison" aria-label="原图头像与目录头像参照">
                <figure>
                  <div class="avatar-source-crop" :style="avatarCropStyle(character)" role="img" :aria-label="`原图头像槽位 ${index + 1}`"></div>
                  <figcaption>原图头像</figcaption>
                </figure>
                <figure v-if="referencePortrait(selectedPanel, character, index)">
                  <img class="avatar-reference" :src="referencePortrait(selectedPanel, character, index)?.image_url" :alt="`${referenceLabel(selectedPanel, character, index)}：${displayedReferenceName(selectedPanel, character, index)}`" />
                  <figcaption>{{ referenceLabel(selectedPanel, character, index) }}</figcaption>
                </figure>
                <small v-else class="avatar-reference-missing">目录中暂无该名称的头像参照</small>
              </div>
              <details class="review-slot-editor">
                <summary>本地人工修正</summary>
                <p class="muted">从角色目录与漂泊者形态中选择，头像本身不能确认漂泊者形态。修正只保存在此浏览器，并与当前原图对应。</p>
                <CharacterSelect
                  :model-value="draftCorrection(selectedPanel, character, index)"
                  :options="portraitOptions"
                  :selected-names="[]"
                  @update:model-value="setDraftCorrection(selectedPanel.id, index, $event)"
                />
                <div class="review-slot-actions">
                  <button class="button secondary" type="button" :disabled="!canSaveCorrection(selectedPanel, character, index)" @click="saveCorrection(selectedPanel, character, index)">保存本地修正</button>
                  <button v-if="hasLocalCorrection(selectedPanel.id, index)" class="button ghost" type="button" @click="revokeCorrection(selectedPanel, index)">撤销本地修正</button>
                </div>
              </details>
            </div>
          </article>
        </section>

        <section class="recognition-source" aria-label="原图位置">
          <h3>原图位置</h3>
          <div class="recognition-crop" :style="cropStyle(selectedPanel)" role="img" :aria-label="`原图裁切：${selectedPanel.id}`"></div>
          <a :href="sourceImageUrl" target="_blank" rel="noreferrer">在原图中查看：x {{ selectedPanel.source_rect.x }}，y {{ selectedPanel.source_rect.y }}，{{ selectedPanel.source_rect.width }} × {{ selectedPanel.source_rect.height }}</a>
        </section>
      </div>

        <label v-if="(selectedPanel.tables?.length ?? 0) > 1" class="recognition-picker">子表范围
          <select v-model.number="selectedTableIndex" aria-label="选择 DPS 子表">
            <option :value="-1">全部子表</option>
            <option v-for="(table, index) in selectedPanel.tables" :key="table.id" :value="index">子表 {{ index + 1 }} · {{ reviewedTables[index]?.title ?? table.id }}</option>
          </select>
        </label>

      <section v-if="reviewedTables.some((table) => table.rows?.length)" class="recognition-verified-tables" aria-labelledby="verified-tables-heading">
        <h3 id="verified-tables-heading">已核对表格</h3>
        <p class="muted">此区展示已对照原图逐行转录的子表。空白沿用原图，“待核”表示无法确认；只供资料查阅，配置含义另需确认后才能参与优化。</p>
        <article v-for="(table, tableIndex) in reviewedTables.filter((item, index) => item.rows?.length && (selectedTableIndex < 0 || index === selectedTableIndex))" :key="`${table.title}-${tableIndex}-verified`" class="manual-review-table">
          <div class="manual-review-heading">
            <h4>{{ table.title }}</h4>
            <div class="manual-review-tags">
              <span v-if="table.author != null">作者：{{ table.author }}</span>
              <span v-if="table.source_text != null">来源：{{ table.source_text }}</span>
              <span v-if="table.difficulty != null">难度：{{ table.difficulty }}</span>
              <span v-if="table.stability != null">稳定性：{{ table.stability }}</span>
              <span v-if="table.numeric_verification">人工视觉转录</span>
            </div>
          </div>
          <p v-if="table.note" class="muted">备注：{{ table.note }}</p>
          <div class="scroll-table manual-review-scroll">
            <table>
              <thead v-if="table.headers?.length"><tr><th v-for="(header, headerIndex) in table.headers" :key="headerIndex">{{ header }}</th></tr></thead>
              <tbody><tr v-for="(row, rowIndex) in table.rows ?? []" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex"><code>{{ cell ?? '待核' }}</code></td></tr></tbody>
            </table>
          </div>
        </article>
      </section>

      <details class="recognition-table-section">
        <summary>查看自动识别原文（含未修正结果）</summary>
        <h3 id="recognition-table-heading">OCR 原文</h3>
        <p class="muted">仅展示 OCR 原始单元格文字；符号、百分号和星号均保持原样，不能视为已校正数值。{{ unitNote }}</p>

        <div v-if="selectedRows.length" class="scroll-table recognition-table">
          <table>
            <tbody>
              <tr v-for="(row, rowIndex) in selectedRows" :key="`${rowIndex}-${row.y ?? ''}`">
                <td v-for="(cell, cellIndex) in row.cells ?? []" :key="cellIndex"><code>{{ cell.raw }}</code></td>
                <td v-if="!row.cells?.length"><code>{{ row.raw_text }}</code></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty-inline">该卡片没有提取到可显示的表格行。</p>
      </details>

      <section v-if="selectedNotes.length" class="recognition-notes" aria-labelledby="recognition-notes-heading">
        <h3 id="recognition-notes-heading">底部区域 OCR 原文（可能含表格尾行）</h3>
        <ul><li v-for="(note, index) in selectedNotes" :key="index">{{ note }}</li></ul>
      </section>

      <details v-if="reviewedTables.some((table) => table.sample_rows?.length)" class="recognition-manual-review">
        <summary>样本人工核对（非全表审核）</summary>
        <p class="muted">仅列出本卡片中已逐项对照原图的样本行；其他 OCR 内容仍需复核，且不会进入优化计算。</p>
        <article v-for="(table, tableIndex) in reviewedTables" :key="`${table.title}-${tableIndex}`" class="manual-review-table">
          <div class="manual-review-heading">
            <h4>{{ table.title }}</h4>
            <div class="manual-review-tags">
              <span v-if="table.author != null">作者：{{ table.author }}</span>
              <span v-if="table.source_text != null">来源：{{ table.source_text }}</span>
              <span v-if="table.difficulty != null">难度：{{ table.difficulty }}</span>
              <span v-if="table.stability != null">稳定性：{{ table.stability }}</span>
            </div>
          </div>
          <div v-if="table.sample_rows?.length" class="scroll-table manual-review-scroll">
            <table>
              <thead v-if="table.headers?.length"><tr><th v-for="(header, headerIndex) in table.headers" :key="headerIndex">{{ header }}</th></tr></thead>
              <tbody><tr v-for="(row, rowIndex) in table.sample_rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex"><code>{{ cell }}</code></td></tr></tbody>
            </table>
          </div>
        </article>
      </details>


    </template>

    <p v-else class="empty-inline">当前筛选没有可审查的卡片。</p>

    <details v-if="readingOrderText" class="recognition-raw-ocr" @toggle="onReadingOrderToggle">
      <summary>查看整张图片的 OCR 阅读顺序文本（{{ document.summary.ocr_token_count ?? '—' }} 个词元）</summary>
      <pre v-if="showReadingOrder">{{ readingOrderText }}</pre>
    </details>
  </section>
</template>

<style scoped>
.recognition-review-controls { display: grid; grid-template-columns: minmax(10rem, .65fr) minmax(0, 1.35fr) auto; gap: .65rem; align-items: end; }
.review-control-actions, .review-slot-actions { display: flex; flex-wrap: wrap; gap: .42rem; }
.review-slot-editor { margin-top: .55rem; padding-top: .48rem; border-top: 1px solid rgba(84, 112, 150, .45); }
.review-slot-editor summary { color: #9fcbff; cursor: pointer; font-size: .78rem; }
.review-slot-editor .muted { margin: .45rem 0; font-size: .76rem; }
.review-slot-actions { margin-top: .48rem; }
.recognition-role.local-correction { border-color: #a06dda; background: #201a36; }
.recognition-verified-tables { display: grid; gap: .65rem; padding-top: .85rem; border-top: 1px solid #2b405d; }
.recognition-verified-tables h3 { margin: 0; }
.recognition-verified-tables > .muted { margin: -.3rem 0 0; }
@media (max-width: 720px) { .recognition-review-controls { grid-template-columns: 1fr; }.review-control-actions { justify-content: flex-start; } }
</style>
