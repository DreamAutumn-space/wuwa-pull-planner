<script setup lang="ts">
import type { ResultTeam } from '../types'

defineProps<{ teams: ResultTeam[] }>()

function members(team: ResultTeam): string {
  return team.members.map((member) => `${member.character} C${member.chain} · ${member.weapon} R${member.refinement}`).join(' / ')
}

function weaponAssignment(value: ResultTeam['weapon_assignment']): string {
  if (!value) return '—'
  if (Array.isArray(value)) {
    return value.map((item) => {
      const character = typeof item.character === 'string' ? item.character : '角色'
      const weapon = typeof item.weapon === 'string' ? item.weapon : '武器'
      const refinement = typeof item.refinement === 'number' ? ` 精${item.refinement}` : ''
      return `${character} → ${weapon}${refinement}`
    }).join('；')
  }
  return Object.entries(value).map(([name, weapon]) => `${name} → ${weapon}`).join('；')
}

function dps(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}
</script>

<template>
  <p v-if="!teams.length" class="empty-inline">后端未返回可显示的队伍。</p>
  <div v-else class="scroll-table">
    <table class="team-table">
      <thead><tr><th>主 C</th><th>成员需求</th><th>轴</th><th>难度</th><th>DPS</th><th>武器分配</th></tr></thead>
      <tbody>
        <tr v-for="team in teams" :key="team.record_id + team.rotation_id">
          <td><strong>{{ team.main_c }}</strong><small>{{ team.record_id }}</small></td>
          <td>{{ members(team) }}</td>
          <td>{{ team.rotation_id }}</td>
          <td><span class="difficulty-tag">{{ team.difficulty }}</span></td>
          <td>{{ dps(team.dps) }}</td>
          <td>{{ weaponAssignment(team.weapon_assignment) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
