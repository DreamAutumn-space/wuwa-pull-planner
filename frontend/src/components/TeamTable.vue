<script setup lang="ts">
import type { ResultTeam } from '../types'

defineProps<{ teams: ResultTeam[] }>()

function members(team: ResultTeam): string {
  return team.members.map((member) => `${member.character} C${member.chain} · ${member.weapon} R${member.refinement}`).join(' / ')
}

function dps(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function perGold(value: ResultTeam['dps_per_gold']): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(value)
    : '—'
}
</script>

<template>
  <p v-if="!teams.length" class="empty-inline">后端未返回可显示的队伍。</p>
  <div v-else class="scroll-table">
    <table class="team-table">
      <thead><tr><th>主 C</th><th>成员需求</th><th>轴</th><th>难度</th><th>DPS</th><th>队伍金数</th><th>每金 DPS</th></tr></thead>
      <tbody>
        <tr v-for="team in teams" :key="team.record_id + team.rotation_id">
          <td><strong>{{ team.main_c }}</strong><small>{{ team.record_id }}</small></td>
          <td>{{ members(team) }}</td>
          <td>{{ team.rotation_id }}</td>
          <td><span class="difficulty-tag">{{ team.difficulty }}</span></td>
          <td>{{ dps(team.dps) }}</td>
          <td>{{ typeof team.gold_count === 'number' ? `${team.gold_count} 金` : '—' }}</td>
          <td>{{ perGold(team.dps_per_gold) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
