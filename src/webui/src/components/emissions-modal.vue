<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import AccountAPI from '@/api/v1/account'

const props = defineProps({
    visible: {
        type: Boolean,
        default: false
    },
    lockedEmission: {
        default: 0n
    }
})

const emit = defineEmits(['update:visible'])

const accountAPI = new AccountAPI()
accountAPI.getHttpClient().apiServerErrorHandler = (msg) => {
    console.error('Vesting list server error:', msg)
}
accountAPI.getHttpClient().apiUnknownErrorHandler = () => {
    console.error('Vesting list unknown error')
}
accountAPI.getHttpClient().apiForbiddenErrorHandler = () => {
    console.error('Vesting list forbidden')
}
const ETHER_WEI = 1000000000000000000n

const vestings = ref([])
const vestingsLoading = ref(false)
const vestingsPagination = ref({
    current: 1,
    pageSize: 20,
    total: 0,
    showSizeChanger: false
})

const vestingColumns = [
    {
        title: 'Total Amount',
        dataIndex: 'total_amount',
        key: 'total_amount'
    },
    {
        title: 'Start Time',
        dataIndex: 'start_time',
        key: 'start_time'
    },
    {
        title: 'Lock Duration',
        dataIndex: 'duration_days',
        key: 'duration_days'
    },
    {
        title: 'Released Amount',
        dataIndex: 'released_amount',
        key: 'released_amount'
    },
    {
        title: 'Remaining Amount',
        dataIndex: 'remaining_amount',
        key: 'remaining_amount'
    },
    {
        title: 'Status',
        dataIndex: 'status',
        key: 'status'
    }
]

function formatTokenAmountWei(value) {
    try {
        const big = BigInt(value || 0)
        const decimals = (big / ETHER_WEI).toString()
        let fractions = ((big * 100n) / ETHER_WEI % 100n).toString()
        while (fractions.length < 2) {
            fractions = '0' + fractions
        }
        return 'CNX ' + decimals + '.' + fractions
    } catch (e) {
        return 'CNX 0.00'
    }
}

function formatDate(t) {
    const n = Number(t)
    if (!Number.isFinite(n) || n === 0) return ''
    const seconds = n > 1e12 ? Math.floor(n / 1000) : Math.floor(n)
    const d = new Date(seconds * 1000)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return y + '-' + m + '-' + day
}

function formatLockedAmount(value) {
    try {
        const big = BigInt(value || 0)
        const decimals = (big / ETHER_WEI).toString()
        let fractions = ((big * 100n) / ETHER_WEI % 100n).toString()
        while (fractions.length < 2) {
            fractions = '0' + fractions
        }
        return decimals + '.' + fractions
    } catch (e) {
        return '0.00'
    }
}

const formattedLockedEmission = computed(() => formatLockedAmount(props.lockedEmission))

async function getVestings(page = 1, pageSize = 20) {
    vestingsLoading.value = true
    try {
        const res = await accountAPI.getVestings(page, pageSize)
        vestings.value = (res.vesting_records || []).map((record) => ({
            ...record,
            slashed: record && record.slashed === true,
            start_time: formatDate(record && record.start_time),
            duration_days: `${record && record.duration_days ? record.duration_days : 0} days`,
            total_amount: formatTokenAmountWei(record && record.total_amount),
            released_amount: formatTokenAmountWei(record && record.released_amount),
            remaining_amount: formatTokenAmountWei(record && record.remaining_amount)
        }))
        vestingsPagination.value.total = res.total || 0
        vestingsPagination.value.current = page
        vestingsPagination.value.pageSize = pageSize
    } catch (e) {
        vestings.value = []
        message.error('Failed to fetch vesting records. Please try again.')
        console.error('Failed to fetch vesting records:', e)
    } finally {
        vestingsLoading.value = false
    }
}

function handleVestingsTableChange(pagination) {
    getVestings(pagination.current, pagination.pageSize)
}

watch(
    () => props.visible,
    (open) => {
        if (open) {
            getVestings(1, vestingsPagination.value.pageSize)
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
        title="Emissions"
        :footer="null"
        :width="920"
        :mask-closable="true"
        @cancel="handleCancel"
    >
        <a-typography-text type="secondary" style="display: block; margin-bottom: 16px">
            Locked Emission: {{ formattedLockedEmission }} CNX
        </a-typography-text>
        <a-table
            :columns="vestingColumns"
            :data-source="vestings"
            :loading="vestingsLoading"
            :pagination="vestingsPagination"
            @change="handleVestingsTableChange"
            row-key="id"
        >
            <template #bodyCell="{ column, record }">
                <template v-if="column.dataIndex === 'status'">
                    <a-tag v-if="record.slashed === true" color="volcano">Slashed</a-tag>
                    <a-tag v-else-if="record.status === 0 || record.status === '0'" color="blue">Active</a-tag>
                    <a-tag v-else-if="record.status === 1 || record.status === '1'" color="green">Completed</a-tag>
                    <span v-else>{{ record.status }}</span>
                </template>
            </template>
        </a-table>
    </a-modal>
</template>
