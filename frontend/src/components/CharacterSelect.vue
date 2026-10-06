<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'

const props = defineProps<{
  modelValue: string
  options: Array<{ value: string; aliases?: string[] }>
  selectedNames: string[]
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const listId = `character-options-${useId()}`
const query = ref(props.modelValue)
const open = ref(false)
const activeIndex = ref(-1)
const hasTypedInvalidName = ref(false)
const listbox = ref<HTMLElement | null>(null)

watch(() => props.modelValue, (value) => {
  query.value = value
  hasTypedInvalidName.value = false
})

const filteredOptions = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('zh-CN')
  const excluded = new Set(props.selectedNames.map((name) => name.trim()))
  return props.options.flatMap((option) => {
    const searchTerms = [option.value, ...(option.aliases ?? [])]
    if (keyword && !searchTerms.some((term) => term.toLocaleLowerCase('zh-CN').includes(keyword))) return []
    const isCurrentRowValue = searchTerms.includes(props.modelValue.trim())
    return [{ option, disabled: excluded.has(option.value) && !isCurrentRowValue }]
  })
})

const unknownCommittedName = computed(() => Boolean(
  props.modelValue.trim() && !props.options.some((option) => [option.value, ...(option.aliases ?? [])].includes(props.modelValue.trim())),
))

function openList(): void {
  open.value = true
  activeIndex.value = filteredOptions.value.findIndex((item) => !item.disabled)
  void scrollActiveIntoView()
}

async function scrollActiveIntoView(): Promise<void> {
  if (activeIndex.value < 0) return
  await nextTick()
  const option = listbox.value?.children.item(activeIndex.value)
  if (option instanceof HTMLElement) option.scrollIntoView({ block: 'nearest' })
}

function selectName(name: string, disabled = false): void {
  if (disabled) return
  emit('update:modelValue', name)
  query.value = name
  open.value = false
  activeIndex.value = -1
  hasTypedInvalidName.value = false
}

function onInput(): void {
  hasTypedInvalidName.value = false
  openList()
}

function onBlur(): void {
  open.value = false
  activeIndex.value = -1
  if (query.value.trim() !== props.modelValue.trim()) {
    hasTypedInvalidName.value = Boolean(query.value.trim())
    query.value = props.modelValue
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!open.value) return openList()
    for (let index = activeIndex.value + 1; index < filteredOptions.value.length; index += 1) {
      if (!filteredOptions.value[index]?.disabled) {
        activeIndex.value = index
        void scrollActiveIntoView()
        break
      }
    }
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) return openList()
    for (let index = activeIndex.value - 1; index >= 0; index -= 1) {
      if (!filteredOptions.value[index]?.disabled) {
        activeIndex.value = index
        void scrollActiveIntoView()
        break
      }
    }
  } else if (event.key === 'Enter' && open.value) {
    event.preventDefault()
    const candidate = filteredOptions.value[activeIndex.value]
    if (candidate) selectName(candidate.option.value, candidate.disabled)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    query.value = props.modelValue
    open.value = false
    activeIndex.value = -1
    hasTypedInvalidName.value = false
  }
}
</script>

<template>
  <div class="character-combobox">
    <input
      v-model="query"
      role="combobox"
      autocomplete="off"
      aria-label="角色名"
      :aria-expanded="open"
      :aria-controls="listId"
      :aria-activedescendant="open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined"
      aria-autocomplete="list"
      placeholder="输入后从目录选择"
      @focus="openList"
      @input="onInput"
      @blur="onBlur"
      @keydown="onKeydown"
    />
    <ul v-if="open" :id="listId" ref="listbox" class="character-options" role="listbox">
      <li v-for="(item, index) in filteredOptions" :id="`${listId}-${index}`" :key="item.option.value" role="option" :aria-selected="index === activeIndex" :aria-disabled="item.disabled" :class="{ active: index === activeIndex, disabled: item.disabled }" @pointerdown.prevent @click="selectName(item.option.value, item.disabled)">
        {{ item.option.value }}<span v-if="item.disabled">已添加</span>
      </li>
      <li v-if="!filteredOptions.length" class="no-options" role="status">没有可选的目录角色</li>
    </ul>
    <small v-if="unknownCommittedName" class="selection-warning">当前名称不在角色目录中；已保留原值，请从候选列表修正。</small>
    <small v-else-if="hasTypedInvalidName" class="selection-warning">未选择目录角色，输入未保存。</small>
  </div>
</template>
