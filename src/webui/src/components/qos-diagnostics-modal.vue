<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
    ArrowDownOutlined,
    ArrowRightOutlined,
    ArrowUpOutlined,
    CopyOutlined,
    MinusOutlined
} from '@ant-design/icons-vue'
import NodeAPI from '@/api/v1/node'

const props = defineProps({
    visible: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['update:visible'])

const nodeAPI = new NodeAPI()
nodeAPI.getHttpClient().apiServerErrorHandler = (msg) => {
    console.error('QoS tracing server error:', msg)
}
nodeAPI.getHttpClient().apiUnknownErrorHandler = () => {
    console.error('QoS tracing unknown error')
}
nodeAPI.getHttpClient().apiForbiddenErrorHandler = () => {
    console.error('QoS tracing forbidden')
}
const loading = ref(false)
const traceData = ref(null)

const events = computed(() => {
    const list = Array.isArray(traceData.value?.events) ? traceData.value.events : []
    return list.slice().sort((a, b) => Number(b.timestamp || 0) - Number(a.timestamp || 0))
})

const eventDescriptionMap = {
    validation_group_rank_1:
        'In a three-node speed comparison, this node finished first. This raises long-term QoS.',
    validation_group_rank_2:
        'In a three-node speed comparison, this node finished second. This slightly lowers long-term QoS.',
    validation_group_rank_3:
        'In a three-node speed comparison, this node finished last. This lowers long-term QoS.',
    validation_group_aborted:
        'In a three-node speed comparison, this node failed to complete successfully. This lowers long-term QoS.',
    task_timeout_penalty: 'The task timed out. Timeouts reduce short-term health immediately.',
    task_result_upload_success_boost:
        'The node uploaded a validated result successfully. This raises short-term health.',
    validation_group_matched_boost:
        'The node completed a task in a validation group successfully. This raises short-term health.',
    node_join_health_reset: 'The node joined or rejoined the network. Short-term health resets to full.',
    node_rejoin_qos_floor:
        'The node rejoined with a low long-term score. The score was raised to the configured minimum.'
}

function getEventLabel(type) {
    return String(type || 'qos_score_update').toUpperCase()
}

function getEventDescription(type) {
    return eventDescriptionMap[type] || 'A QoS score changed for this node.'
}

function formatTimestamp(timestamp) {
    const value = Number(timestamp || 0)
    if (!value) return '-'
    return new Date(value * 1000).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    })
}

function shortenTaskId(value) {
    if (!value) return ''
    const text = String(value)
    if (text.length <= 10) return text
    return text.slice(0, 5) + '...' + text.slice(-5)
}

function formatScore(value) {
    const score = Number(value)
    if (!Number.isFinite(score)) return '-'
    return score.toFixed(4).replace(/\.?0+$/, '')
}

function formatPercentScore(value) {
    const score = Number(value)
    if (!Number.isFinite(score)) return '-'
    return (score * 100).toFixed(1).replace(/\.0$/, '') + '%'
}

function getScoreChange(before, after) {
    const beforeNumber = Number(before)
    const afterNumber = Number(after)
    if (!Number.isFinite(beforeNumber) || !Number.isFinite(afterNumber)) {
        return { direction: 'flat', delta: '-', className: 'flat' }
    }

    const delta = afterNumber - beforeNumber
    if (delta > 0) {
        return { direction: 'up', delta: '+' + formatScore(delta), className: 'up' }
    }
    if (delta < 0) {
        return { direction: 'down', delta: formatScore(delta), className: 'down' }
    }
    return { direction: 'flat', delta: '0', className: 'flat' }
}

function getScoreRows(event) {
    return [
        {
            key: 'long',
            label: 'Long-Term QoS',
            before: event.qos_long_before,
            after: event.qos_long_after
        },
        {
            key: 'short',
            label: 'Short-Term QoS',
            before: event.qos_short_before,
            after: event.qos_short_after
        },
        {
            key: 'overall',
            label: 'Overall QoS',
            before: event.qos_before,
            after: event.qos_after
        }
    ].map((row) => ({
        ...row,
        change: getScoreChange(row.before, row.after)
    }))
}

async function copyTaskId(value) {
    if (!value) return
    try {
        await navigator.clipboard.writeText(String(value))
        message.success('Task ID Commitment copied.')
    } catch (e) {
        message.error('Failed to copy Task ID Commitment.')
        console.error('Failed to copy Task ID Commitment:', e)
    }
}

async function fetchQosTracing() {
    loading.value = true
    try {
        traceData.value = await nodeAPI.getQosTracing()
    } catch (e) {
        traceData.value = null
        message.error('Failed to load QoS diagnostics. Please try again.')
        console.error('Failed to load QoS diagnostics:', e)
    } finally {
        loading.value = false
    }
}

watch(
    () => props.visible,
    (open) => {
        if (open) {
            fetchQosTracing()
        }
    }
)

function handleCancel() {
    emit('update:visible', false)
}
</script>

<template>
    <a-modal
        :visible="visible"
        title="QoS Diagnostics"
        :footer="null"
        :width="920"
        :mask-closable="true"
        @cancel="handleCancel"
    >
        <div class="qos-diagnose-container">
            <div class="page-header">
                <div>
                    <p>Review recent quality score changes recorded for your node.</p>
                    <p class="docs-hint">
                        Learn more about the QoS design in the
                        <a
                            href="https://docs.crynux.io/system-design/task-dispatching#node-selection-probability"
                            target="_blank"
                            rel="noopener noreferrer"
                            >Crynux documentation</a
                        >.
                    </p>
                </div>
            </div>

            <a-spin :spinning="loading">
                <a-empty
                    v-if="!loading && events.length === 0"
                    description="No QoS events have been recorded for this node yet."
                />

                <a-list v-else item-layout="vertical" :data-source="events" class="event-list">
                    <template #renderItem="{ item }">
                        <a-list-item class="timeline-item">
                            <div class="timeline-time">{{ formatTimestamp(item.timestamp) }}</div>
                            <div class="timeline-node"></div>
                            <div class="event-card">
                                <div class="event-header">
                                    <div>
                                        <div class="event-title-row">
                                            <span class="event-title">{{ getEventLabel(item.event_type) }}</span>
                                        </div>
                                        <div class="event-description">{{ getEventDescription(item.event_type) }}</div>
                                    </div>
                                    <a-tooltip v-if="item.task_id_commitment" title="Task ID Commitment">
                                        <a-input-group compact class="task-id-group">
                                            <a-input
                                                class="task-id-input"
                                                :value="shortenTaskId(item.task_id_commitment)"
                                                disabled
                                            />
                                            <a-button class="task-id-copy" @click.stop="copyTaskId(item.task_id_commitment)">
                                                <template #icon><CopyOutlined /></template>
                                            </a-button>
                                        </a-input-group>
                                    </a-tooltip>
                                </div>

                                <div class="score-row">
                                    <div
                                        v-for="row in getScoreRows(item)"
                                        :key="row.key"
                                        class="score-item"
                                        :class="row.key"
                                    >
                                        <span class="score-direction" :class="row.change.className">
                                            <ArrowUpOutlined v-if="row.change.direction === 'up'" />
                                            <ArrowDownOutlined v-else-if="row.change.direction === 'down'" />
                                            <MinusOutlined v-else />
                                        </span>
                                        <span class="score-label">{{ row.label }}</span>
                                        <div class="score-values">
                                            <span>{{ formatPercentScore(row.before) }}</span>
                                            <ArrowRightOutlined class="score-arrow" />
                                            <span class="score-after" :class="row.change.className">{{
                                                formatPercentScore(row.after)
                                            }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </a-list-item>
                    </template>
                </a-list>
            </a-spin>
        </div>
    </a-modal>
</template>

<style lang="stylus" scoped>
.qos-diagnose-container
  padding 0

.page-header
  margin-bottom 16px

  p
    margin 0
    color #666

  .docs-hint
    margin-top 8px
    font-size 13px

    a
      color #1677ff

.event-list
  margin-top 4px

.event-list
  :deep(.ant-list-item)
    border-block-end none

.timeline-item
  display grid
  grid-template-columns 118px 22px minmax(0, 1fr)
  gap 10px
  align-items stretch
  padding 0 0 12px !important

.timeline-time
  padding-top 14px
  color #8c8c8c
  font-size 12px
  line-height 1.35
  text-align right

.timeline-node
  position relative

  &::before
    content ''
    position absolute
    top -2px
    bottom -14px
    left 10px
    width 2px
    background #e6f4ff

  &::after
    content ''
    position absolute
    top 17px
    left 5px
    width 12px
    height 12px
    border 2px solid #1677ff
    border-radius 50%
    background #fff
    box-shadow 0 0 0 3px #e6f4ff

.event-card
  padding 14px 16px
  border 1px solid #f0f0f0
  border-radius 10px
  background #fff

.event-header
  display flex
  justify-content space-between
  align-items flex-start
  gap 16px
  margin-bottom 10px

.task-id-group
  flex 0 0 auto
  width 128px
  display inline-flex

  :deep(.task-id-input.ant-input)
    width 92px
    height 24px
    padding 0 6px
    color #8c8c8c
    font-size 11px
    background #fafafa
    cursor help

  :deep(.task-id-copy.ant-btn)
    width 32px
    height 24px
    padding 0
    color #8c8c8c
    font-size 11px
    background #fafafa

.event-title-row
  display flex
  align-items center
  flex-wrap wrap
  gap 8px

.event-title
  color #1f1f1f
  font-size 16px
  font-weight 600

.event-description
  margin-top 6px
  color #666
  font-size 13px

.score-row
  display flex
  align-items center
  gap 12px
  flex-wrap nowrap
  overflow-x auto
  padding-top 2px

.score-item
  display inline-flex
  align-items center
  gap 7px
  min-width max-content
  padding 5px 9px
  border-radius 8px

  &.long
    background #e6f4ff

  &.short
    background #fff7e6

  &.overall
    background #f6ffed

.score-direction
  display inline-flex
  align-items center
  font-size 12px

.score-label
  color #666
  font-size 12px

.score-values
  display flex
  align-items center
  gap 5px
  color #595959
  font-size 13px
  font-weight 600

.score-arrow
  color #bfbfbf
  font-size 11px

.score-after.up,
.score-direction.up
  color #389e0d

.score-after.down,
.score-direction.down
  color #cf1322

.score-after.flat,
.score-direction.flat
  color #8c8c8c

@media (max-width: 768px)
  .timeline-item
    grid-template-columns 84px 18px minmax(0, 1fr)
    gap 8px

  .timeline-time
    font-size 11px
    text-align right

  .timeline-node
    &::before
      left 8px

    &::after
      left 3px

  .event-header
    flex-direction column

  .score-row
    flex-direction column
    align-items stretch
    gap 8px
    overflow-x visible

  .score-item
    justify-content space-between
    width 100%
</style>
