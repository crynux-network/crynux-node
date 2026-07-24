<script setup>
import { computed, createVNode, h, onBeforeUnmount, onMounted, reactive, ref, watch, nextTick } from 'vue'
import {
    PauseCircleOutlined,
    LogoutOutlined,
    PlayCircleOutlined,
    ExclamationCircleOutlined,
    CopyOutlined,
    QuestionCircleOutlined,
    EditOutlined
} from '@ant-design/icons-vue'
import { Grid, Modal } from 'ant-design-vue'
import EditAccount from './edit-account.vue'
import GithubButton from 'vue-github-button'

import SystemAPI from '../api/v1/system'
import SettingsAPI from '../api/v1/settings'
import NodeAPI from '../api/v1/node'
import TaskAPI from '../api/v1/task'
import AccountAPI from '../api/v1/account'
import config from '../config.json'
import logger from '../log/log'
import { useSystemStore } from '@/stores/system'
/* global APP_VERSION, BigInt */

const systemStore = useSystemStore()

const showSuccessAlert = ref(false)
const isSaving = ref(false)
const isFlushingTaskErrors = ref(false)
const taskErrorFlushResult = ref(null)
const hasLoadedAccountBalance = ref(false)
const hasLoadedNodeStatus = ref(false)
const minStakingAmountWei = ref(null)
const minStakingAmountLoadError = ref('')
const ETHER_WEI = 1000000000000000000n

const minStakingAmountCNX = computed(() => {
    if (minStakingAmountWei.value === null) return null
    try {
        return Number((BigInt(minStakingAmountWei.value) + ETHER_WEI - 1n) / ETHER_WEI)
    } catch (e) {
        return null
    }
})

const stakingAmountHelp = computed(() => {
    if (minStakingAmountLoadError.value) return minStakingAmountLoadError.value
    if (minStakingAmountCNX.value === null) return 'Loading minimum staking amount from chain.'
    if (isStakingAmountValid.value) return `Minimum staking amount is ${minStakingAmountCNX.value} CNX.`
    return `Staking amount must be an integer and cannot be less than ${minStakingAmountCNX.value}.`
})

const isStakingAmountValid = computed(() => {
    if (typeof settingsInModal.staking_amount !== 'number') {
        return false
    }
    if (!Number.isInteger(settingsInModal.staking_amount)) {
        return false
    }
    if (minStakingAmountCNX.value === null) {
        return false
    }
    return settingsInModal.staking_amount >= minStakingAmountCNX.value
})

// Whether each field changed in Settings modal
const isStakingChanged = computed(() => settingsInModal.staking_amount !== settings.staking_amount)
const isDelegatorShareChanged = computed(() => settingsInModal.delegator_share !== accountStatus.delegator_share)
const isTaskErrorReportChanged = computed(() => settingsInModal.task_error_report_automatic !== settings.task_error_report_automatic)

// Compute dynamic gas and staking requirements for saving settings
const stakingAmountDesiredWei = computed(() => {
    if (typeof settingsInModal.staking_amount !== 'number') return 0n
    try {
        return BigInt(settingsInModal.staking_amount) * ETHER_WEI
    } catch (e) {
        return 0n
    }
})

// Whether saving staking now will deduct tokens from wallet:
// only when node is running or paused, and staking amount is changed.
const stakeDeductsNow = computed(() => isStakingChanged.value && isNodeJoined.value)

// Additional stake that must come from wallet if target is higher than currently staked
const additionalStakeRequiredWei = computed(() => {
    const desired = stakingAmountDesiredWei.value
    const currentStaked = accountStatus.staking || 0n
    if (!stakeDeductsNow.value) return 0n
    const delta = desired - currentStaked
    return delta > 0n ? delta : 0n
})

// Gas requirement: use a single minimal safe gas threshold constant
const requiredTotalWeiForSave = computed(() => GAS_FEE_MIN_WEI + additionalStakeRequiredWei.value)
const hasEnoughForSave = computed(() => {
    if (!accountStatus.address) return false
    return (accountStatus.balance || 0n) >= requiredTotalWeiForSave.value
})

const hasEnoughGas = computed(() => {
    if (!accountStatus.address) return false
    // Consider that additional staking (if any) also consumes wallet balance, leaving gas insufficient.
    return (accountStatus.balance || 0n) >= (GAS_FEE_MIN_WEI + additionalStakeRequiredWei.value)
})

// Save disabled rule: no changes OR invalid staking OR insufficient funds for gas/staking
const isSettingsSaveDisabled = computed(() => {
    const changed = isStakingChanged.value || isDelegatorShareChanged.value || isTaskErrorReportChanged.value
    if (!changed) return true
    if (isStakingChanged.value && !isStakingAmountValid.value) return true
    if ((isStakingChanged.value || isDelegatorShareChanged.value) && !hasEnoughForSave.value) return true
    return false
})

const settingsInsufficientText = computed(() => {
    if (hasEnoughGas.value) return ''
    return 'Not enough tokens to cover gas fee.'
})

const appVersion = APP_VERSION

const accountAPI = new AccountAPI()
const nodeAPI = new NodeAPI()
const systemAPI = new SystemAPI()
const taskAPI = new TaskAPI()
const settingsAPI = new SettingsAPI()

const accountEditor = ref(null)
const showCnxTokenModal = ref(false)
const showWithdrawModal = ref(false)

const topRow = ref(null)
const alertsRow = ref(null)
const cardsRow1 = ref(null)
const cardsRow2 = ref(null)
const cardsRowDelegators = ref(null)

const systemInfo = reactive({
    gpu: {
        usage: 0,
        model: '',
        vram_used_mb: 0,
        vram_total_mb: 0
    },
    cpu: {
        usage: 0,
        num_cores: 0,
        frequency_mhz: 0,
        description: ''
    },
    memory: {
        available_mb: 0,
        total_mb: 0
    },
    disk: {
        hf_models: 0,
        external_models: 0,
        logs: 0,
        temp_files: 0
    }
})

const nodeStatus = reactive({
    status: nodeAPI.NODE_STATUS_INITIALIZING,
    init_message: '',
    message: '',
    tx_status: '',
    tx_error: '',
    slashed: false
})

const accountStatus = reactive({
    address: '',
    balance: 0,
    staking: 0,
    relay_balance: 0,
    delegator_staking: 0,
    delegator_share: 0,
    delegator_num: 0,
    today_delegator_earnings: 0,
    total_delegator_earnings: 0,
    is_loaded: false
})

const taskStatus = reactive({
    status: 'waiting',
    num_today: 0,
    num_total: 0
})


const settings = reactive({
    staking_amount: null,
    task_error_report_automatic: false,
    pending_task_error_reports: 0
})

const settingsInModal = reactive({
    staking_amount: null,
    delegator_share: 0,
    task_error_report_automatic: false
})


const runnerVersion = ref('')

const nodeScores = reactive({
    staking: 0,
    qos: 0,
    prob_weight: 0
})

let apiContinuousErrorCount = reactive({
    'account': 0,
    'node': 0,
    'system': 0,
    'task': 0,
    'settings': 0
})

const apiErrorHandler = (apiName) => {
    return () => {
        apiContinuousErrorCount[apiName]++
        logger.error('API Error: ', apiName)
    }
}

const accountAPIErrorHandler = apiErrorHandler('account')
accountAPI.getHttpClient().apiServerErrorHandler = accountAPIErrorHandler
accountAPI.getHttpClient().apiUnknownErrorHandler = accountAPIErrorHandler
accountAPI.getHttpClient().apiForbiddenErrorHandler = accountAPIErrorHandler

const nodeAPIErrorHandler = apiErrorHandler('node')
nodeAPI.getHttpClient().apiServerErrorHandler = nodeAPIErrorHandler
nodeAPI.getHttpClient().apiUnknownErrorHandler = nodeAPIErrorHandler
nodeAPI.getHttpClient().apiForbiddenErrorHandler = nodeAPIErrorHandler

const systemAPIErrorHandler = apiErrorHandler('system')
systemAPI.getHttpClient().apiServerErrorHandler = systemAPIErrorHandler
systemAPI.getHttpClient().apiUnknownErrorHandler = systemAPIErrorHandler
systemAPI.getHttpClient().apiForbiddenErrorHandler = systemAPIErrorHandler

const taskAPIErrorHandler = apiErrorHandler('task')
taskAPI.getHttpClient().apiServerErrorHandler = taskAPIErrorHandler
taskAPI.getHttpClient().apiUnknownErrorHandler = taskAPIErrorHandler
taskAPI.getHttpClient().apiForbiddenErrorHandler = taskAPIErrorHandler

const shortAddress = computed(() => {
    if (accountStatus.address === '') {
        return 'N/A'
    } else {
        return (
            accountStatus.address.substring(0, 7) +
            '...' +
            accountStatus.address.substring(accountStatus.address.length - 5)
        )
    }
})

// Node status helpers
const isNodeInitializing = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_INITIALIZING)
const isNodeRunning = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_RUNNING)
const isNodePaused = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_PAUSED)
const isNodeError = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_ERROR)
const isNodePendingPause = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_PENDING_PAUSE)
const isNodePendingStop = computed(() => nodeStatus.status === nodeAPI.NODE_STATUS_PENDING_STOP)
const isNodeStoppedLike = computed(() =>
    [nodeAPI.NODE_STATUS_STOPPED, nodeAPI.NODE_STATUS_SLASHED, nodeAPI.NODE_STATUS_KICKED_OUT].includes(nodeStatus.status)
)
const isNodePending = computed(() => isNodePendingPause.value || isNodePendingStop.value)

const isNodeJoined = computed(() => {
    return isNodeRunning.value || isNodePaused.value || isNodePending.value
})

const GAS_FEE_MIN_WEI = BigInt(config.gas_fee_min_wei)
const hasLoadedWalletFundingState = computed(() => hasLoadedAccountBalance.value && hasLoadedNodeStatus.value)
const hasLoadedStakingAmount = computed(() => typeof settings.staking_amount === 'number')
const stakingAmountText = computed(() => {
    if (!hasLoadedStakingAmount.value) return 'Loading...'
    return `${settings.staking_amount} CNX`
})

const toEtherValue = (value) => {
    try {
        const big = BigInt(value)
        if (big === 0n) return '0'
        const decimals = (big / ETHER_WEI).toString()
        let fractions = ((big / 100000000000000n) % 10000n).toString()
        while (fractions.length < 4) {
            fractions = '0' + fractions
        }
        return decimals + '.' + fractions
    } catch (e) {
        return '0'
    }
}

const trimTrailingZeros = (val) => {
    if (typeof val !== 'string') return val
    if (val.indexOf('.') === -1) return val
    let [i, f] = val.split('.')
    f = f.replace(/0+$/, '')
    return f.length ? i + '.' + f : i
}

const gasEnough = () => {
    if (!accountStatus.address) return false
    return accountStatus.balance >= GAS_FEE_MIN_WEI
}

const startEnough = () => {
    if (!accountStatus.address) return false
    if (typeof settings.staking_amount !== 'number') return false
    const required = BigInt(settings.staking_amount) * ETHER_WEI + GAS_FEE_MIN_WEI
    return accountStatus.balance + accountStatus.staking >= required
}

const requiredStartTotalCNX = computed(() => {
    if (typeof settings.staking_amount !== 'number') return ''
    const stakingWei = BigInt(settings.staking_amount) * ETHER_WEI
    const totalWei = stakingWei + GAS_FEE_MIN_WEI
    return toEtherValue(totalWei)
})

const requiredStartTotalCNXAlert = computed(() => trimTrailingZeros(requiredStartTotalCNX.value))
const gasFeeMinCNX = computed(() => toEtherValue(GAS_FEE_MIN_WEI))
const gasFeeMinCNXAlert = computed(() => trimTrailingZeros(gasFeeMinCNX.value))

const privateKeyUpdated = async () => {
    logger.debug('received privateKeyUpdated')
    await updateUI()
}

let fixedBottomBar = ref(false)

const windowResized = () => {
    const bottomBar = document.getElementById("bottom-bar")
    if (!bottomBar || !topRow.value || !alertsRow.value || !cardsRow1.value || !cardsRow2.value) {
        return
    }

    const topRowHeight = topRow.value.$el.offsetHeight;
    const alertsRowHeight = alertsRow.value.$el.offsetHeight;
    const cardsRow1Height = cardsRow1.value.$el.offsetHeight;
    const cardsRow2Height = cardsRow2.value.$el.offsetHeight;
    const cardsRowDelegatorsHeight = cardsRowDelegators.value ? cardsRowDelegators.value.$el.offsetHeight : 0;

    // There is a 16px margin-top before the delegators row (when visible) and before the second row of cards.
    const mainContentHeight =
        topRowHeight +
        alertsRowHeight +
        cardsRow1Height +
        16 +
        cardsRowDelegatorsHeight +
        (cardsRowDelegatorsHeight > 0 ? 16 : 0) +
        cardsRow2Height;

    const bottomBarHeight = bottomBar.offsetHeight;
    // There is a 68px margin-top on the bottom bar when it is not fixed.
    const bottomBarMargin = 68;

    const totalContentHeight = mainContentHeight + bottomBarMargin + bottomBarHeight;

    const windowHeight = window.innerHeight;

    fixedBottomBar.value = windowHeight > totalContentHeight;
}

const accountBalance = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.balance)
    }
})

const relayBalance = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.relay_balance)
    }
})

const accountStaked = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.staking)
    }
})

const delegatorEarningsToday = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.today_delegator_earnings)
    }
})

const delegatorEarningsTotal = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.total_delegator_earnings)
    }
})

const delegatorStaked = computed(() => {
    if (accountStatus.address === '') {
        return '0'
    } else {
        return toEtherValue(accountStatus.delegator_staking)
    }
})

let uiUpdateInterval = null
let uiUpdateCurrentTicket = null
onMounted(async () => {

    windowResized()
    addEventListener("resize", windowResized);

    try {
        await updateUI()
    } catch (e) {
        logger.error('First time UI update failed')
    }

    if (uiUpdateInterval != null) {
        clearInterval(uiUpdateInterval)
    }

    uiUpdateInterval = setInterval(updateUI, 5000)
})

watch(() => systemStore.showSettingsModal, (newValue) => {
    if (newValue) {
        Object.assign(settingsInModal, settings)
        // Delegator share comes from account info, not settings API.
        settingsInModal.delegator_share = accountStatus.delegator_share
        showSuccessAlert.value = false
        taskErrorFlushResult.value = null
        updateMinStakingAmount()
    }
})

onBeforeUnmount(() => {
    clearInterval(uiUpdateInterval)
    uiUpdateInterval = null
})

const handleSettingsSave = async () => {
    // Only send API requests for fields that actually changed; otherwise close the dialog.
    const changedStaking = settingsInModal.staking_amount !== settings.staking_amount
    const changedDelegatorShare = settingsInModal.delegator_share !== accountStatus.delegator_share
    const changedTaskErrorReport = settingsInModal.task_error_report_automatic !== settings.task_error_report_automatic

    if (!changedStaking && !changedDelegatorShare && !changedTaskErrorReport) {
        systemStore.showSettingsModal = false
        return
    }

    isSaving.value = true;
    try {
        logger.info('Save settings', settingsInModal)

        // Update staking amount if changed
        if (changedStaking) {
            await settingsAPI.updateSettings({ staking_amount: settingsInModal.staking_amount })
            apiContinuousErrorCount['settings'] = 0
            settings.staking_amount = settingsInModal.staking_amount
        }

        if (changedTaskErrorReport) {
            await settingsAPI.updateSettings({
                task_error_report_automatic: settingsInModal.task_error_report_automatic
            })
            apiContinuousErrorCount['settings'] = 0
            settings.task_error_report_automatic = settingsInModal.task_error_report_automatic
        }

        // Update delegator share if changed
        if (changedDelegatorShare) {
            await accountAPI.updateDelegatorShare(settingsInModal.delegator_share)
            apiContinuousErrorCount['account'] = 0
            accountStatus.delegator_share = settingsInModal.delegator_share
        }

        showSuccessAlert.value = true
        setTimeout(() => {
            showSuccessAlert.value = false
        }, 5000)
    } catch (e) {
        logger.error("Updating settings failed:")
        logger.error(e)
    } finally {
        isSaving.value = false
    }
}

const handleSettingsCancel = () => {
    systemStore.showSettingsModal = false
}

const handleTaskErrorReportFlush = async () => {
    isFlushingTaskErrors.value = true
    taskErrorFlushResult.value = null
    try {
        const result = await settingsAPI.flushTaskErrorReports()
        settings.pending_task_error_reports = result.remaining
        taskErrorFlushResult.value = {
            type: 'success',
            message: `Reported ${result.reported}; ${result.remaining} remaining.`
        }
    } catch (e) {
        taskErrorFlushResult.value = {
            type: 'error',
            message: 'Task error reports could not be sent. Please try again.'
        }
        logger.error('Flushing task error reports failed:')
        logger.error(e)
    } finally {
        isFlushingTaskErrors.value = false
    }
}

async function updateMinStakingAmount() {
    minStakingAmountWei.value = null
    minStakingAmountLoadError.value = ''
    try {
        const minStakingAmountResp = await settingsAPI.getMinStakingAmount()
        minStakingAmountWei.value = minStakingAmountResp.min_staking_amount
        if (settingsInModal.staking_amount === null && minStakingAmountCNX.value !== null) {
            settingsInModal.staking_amount = minStakingAmountCNX.value
        }
    } catch (e) {
        minStakingAmountLoadError.value = 'Cannot load the minimum staking amount from chain. Please try again later.'
        logger.error('Updating minimum staking amount failed:')
        logger.error(e)
    }
}


const updateUI = async () => {
    uiUpdateCurrentTicket = Date.now()

    try {
        await updateSettings(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating settings failed:")
        logger.error(e)
    }

    try {
        await updateTaskStats(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating task stats failed:")
        logger.error(e)
    }

    try {
        await updateUIWithTicket(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating UI failed:")
        logger.error(e)
    }

    try {
        await updateSystemInfo(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating system info failed:")
        logger.error(e)
    }

    try {
        await updateRunnerVersion(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating runner version failed:")
        logger.error(e)
    }

    try {
        await updateNodeScores(uiUpdateCurrentTicket)
    } catch (e) {
        logger.error("Updating node scores failed:")
        logger.error(e)
    }
    nextTick(() => {
        windowResized()
    })
}

const updateSettings = async (ticket) => {
    const settingsResp = await settingsAPI.getSettings()
    apiContinuousErrorCount['settings'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        Object.assign(settings, settingsResp)
    }
}

const updateUIWithTicket = async (ticket) => {

    logger.debug('[' + ticket + '] Updating UI')

    if (isTxSending.value) return
    await updateAccountInfo(ticket)

    if (ticket !== uiUpdateCurrentTicket) {
        logger.debug('[' + ticket + '] Ticket is old. Do not use this data to update UI.')
        return
    }

    if (accountStatus.address === '') {
        logger.debug('[' + ticket + '] Account address is empty. Show edit account dialog.')
        accountEditor.value.showModal()
    } else {
        logger.debug('[' + ticket + '] Account address is not empty. Continue updating network info.')
        await updateNetworkInfo(ticket)
    }
}

const updateAccountInfo = async (ticket) => {

    logger.debug('[' + ticket + '] Updating account info')

    const accountResp = await accountAPI.getAccountInfo()

    logger.debug('[' + ticket + '] Retrieved account address: ' + accountResp.address)

    apiContinuousErrorCount['account'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        logger.debug('[' + ticket + '] Ticket is latest. Update the data.')
        const normalized = {
            address: accountResp.address || '',
            balance: BigInt(accountResp.balance ?? 0),
            staking: BigInt(accountResp.staking ?? 0),
            relay_balance: BigInt(accountResp.relay_balance ?? 0),
            delegator_staking: BigInt(accountResp.delegator_staking ?? 0),
            delegator_share: parseInt(accountResp.delegator_share ?? 0),
            delegator_num: parseInt(accountResp.delegator_num ?? 0),
            today_delegator_earnings: BigInt(accountResp.today_delegator_earnings ?? 0),
            total_delegator_earnings: BigInt(accountResp.total_delegator_earnings ?? 0),
            is_loaded: Boolean(accountResp.is_loaded)
        }
        Object.assign(accountStatus, normalized)
        hasLoadedAccountBalance.value = normalized.is_loaded
    } else {
        logger.debug('[' + ticket + '] Ticket is old. Discard the response')
    }
}

const updateNetworkInfo = async (ticket) => {

    const nodeResp = await nodeAPI.getNodeStatus()
    apiContinuousErrorCount['node'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        Object.assign(nodeStatus, nodeResp)
        hasLoadedNodeStatus.value = true
    }
}

const updateSystemInfo = async (ticket) => {
    const systemResp = await systemAPI.getSystemInfo()
    apiContinuousErrorCount['system'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        Object.assign(systemInfo, systemResp)
    }
}

const updateTaskStats = async (ticket) => {
    const taskResp = await taskAPI.getTaskRunningStatus()
    apiContinuousErrorCount['task'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        Object.assign(taskStatus, taskResp)
    }
}

const updateRunnerVersion = async (ticket) => {
    const runnerVersionResp = await nodeAPI.getRunnerVersion()
    apiContinuousErrorCount['node'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        runnerVersion.value = runnerVersionResp.version
    }
}

const updateNodeScores = async (ticket) => {
    const nodeScoresResp = await nodeAPI.getNodeScores()
    apiContinuousErrorCount['node'] = 0

    if (ticket === uiUpdateCurrentTicket) {
        Object.assign(nodeScores, nodeScoresResp)
    }
}

let isTxSending = ref(false)
const sendNodeAction = async (action) => {
    if (isTxSending.value) {
        return
    }

    if (action === 'pause') {
        await sendNodeActionAfterConfirmation(
            action,
            'Are you sure to pause the node? You could resume the running later.'
        )
    } else if (action === 'stop') {
        await sendNodeActionAfterConfirmation(
            action,
            'Are you sure to stop the node? The staked tokens will be returned. You could start the node later.'
        )
    } else {
        await doSendNodeAction(action)
    }
}

const sendNodeActionAfterConfirmation = async (action, message) => {
    Modal.confirm({
        title: 'Confirm node operation',
        icon: createVNode(ExclamationCircleOutlined),
        content: message,
        onOk() {
            return doSendNodeAction(action)
        },

        onCancel() {
        }
    })
}

const doSendNodeAction = async (action) => {
    isTxSending.value = true
    try {
        await nodeAPI.sendNodeAction(action)
        await updateUI()
    } finally {
        isTxSending.value = false
    }
}

const useBreakpoint = Grid.useBreakpoint
const screens = useBreakpoint()

const getPercent = (num) => {
    if (num >= 100) {
        return 99
    }

    return num
}

const copyText = async (text) => {
    return navigator.clipboard.writeText(text)
}

const formatBytes = (kb) => {
    if (typeof kb !== 'number' || isNaN(kb)) return { value: '0', unit: 'KB' };

    const value = parseFloat(kb);

    if (value < 1024) {
        return { value: value.toLocaleString('en-US'), unit: 'KB' };
    } else if (value < 1024 * 1024) {
        return { value: (value / 1024).toLocaleString('en-US', { maximumFractionDigits: 2 }), unit: 'MB' };
    } else {
        return { value: (value / (1024 * 1024)).toLocaleString('en-US', { maximumFractionDigits: 2 }), unit: 'GB' };
    }
}

const hfModelsFormatted = computed(() => formatBytes(systemInfo.disk.hf_models));
const externalModelsFormatted = computed(() => formatBytes(systemInfo.disk.external_models));
const logsFormatted = computed(() => formatBytes(systemInfo.disk.logs));
const tempFilesFormatted = computed(() => formatBytes(systemInfo.disk.temp_files));

</script>

<template>
    <a-row ref="topRow" class="top-row"></a-row>
    <a-row ref="alertsRow">
        <a-col
            :xs="{ span: 22, offset: 1, order: 1 }"
            :sm="{ span: 22, offset: 1, order: 1 }"
            :md="{ span: 22, offset: 1, order: 1 }"
            :lg="{ span: 14, offset: 5, order: 1 }"
            :xl="{ span: 14, offset: 5, order: 1 }"
            :xxl="{ span: 14, offset: 5, order: 1 }"
        >
            <a-alert
                message="System is initializing..."
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_INITIALIZING && nodeStatus.init_message === ''"
            ></a-alert>
            <a-alert
                :message="nodeStatus.init_message"
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_INITIALIZING && nodeStatus.init_message !== ''"
            ></a-alert>
            <a-alert
                type="error"
                :message="nodeStatus.message"
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_ERROR"
            ></a-alert>
            <a-alert
                type="error"
                :message="'Cannot get account info from node due to network error, retrying...'"
                class="top-alert"
                v-if="apiContinuousErrorCount['account'] >= 3"
            ></a-alert>
            <a-alert
                type="error"
                :message="'Cannot get node status from node due to network error, retrying...'"
                class="top-alert"
                v-if="apiContinuousErrorCount['node'] >= 3"
            ></a-alert>
            <a-alert
                type="error"
                :message="'Cannot get system info from node due to network error, retrying...'"
                class="top-alert"
                v-if="apiContinuousErrorCount['system'] >= 3"
            ></a-alert>
            <a-alert
                type="error"
                :message="'Cannot get task info from node due to network error, retrying...'"
                class="top-alert"
                v-if="apiContinuousErrorCount['task'] >= 3"
            ></a-alert>
            <a-alert
                type="error"
                :message="'Transaction error: ' + nodeStatus.tx_error + '. Please try again later.'"
                class="top-alert"
                v-if="nodeStatus.tx_status === nodeAPI.TX_STATUS_ERROR"
            ></a-alert>
            <a-alert
                message="Waiting for the Blockchain confirmation..."
                class="top-alert"
                v-if="nodeStatus.tx_status === nodeAPI.TX_STATUS_PENDING"
            ></a-alert>
            <a-alert
                message="Node will stop after finishing the current task"
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_PENDING_STOP"
            ></a-alert>
            <a-alert
                message="Node will pause after finishing the current task"
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_PENDING_PAUSE"
            ></a-alert>
            <a-alert
                type="error"
                message="Node was slashed"
                class="top-alert"
                v-if="nodeStatus.slashed"
            >
                <template #action>
                    <a-button size="small" type="primary" :href="config.discord_link" target="_blank">Crynux Discord</a-button>
                </template>
                <template #description>
                    <div>Your staked tokens have been temporarily moved to the DAO Treasury.</div>
                    <div>A very small number of honest nodes may be slashed due to current technical limitations; a fix is in progress.</div>
                    <div>The council has already been notified. If you have not engaged in malicious activity, your tokens will be returned within 1–2 days. No action is required on your side.</div>
                    <div>Join our Discord to track progress:
                        <a-typography-link :href="config.discord_link" target="_blank">{{ config.discord_link }}</a-typography-link>
                    </div>
                </template>
            </a-alert>
            <a-alert
                type="error"
                message="Node was kicked out"
                class="top-alert"
                v-if="nodeStatus.status === nodeAPI.NODE_STATUS_KICKED_OUT"
            >
                <template #description>
                    <div>Your node was forced to stop because its Quality of Service (QoS) score fell below the required threshold.</div>
                    <div>A low QoS score is typically caused by slow task submission, which can happen with a slow or unstable network connection, limited VRAM, or lower GPU frequency.</div>
                    <div>Please review and improve your hardware and network setup, then rejoin the network after the performance issues are resolved. For more details, see the QoS documentation:
                        <a-typography-link href="https://docs.crynux.io/system-design/quality-of-service-qos" target="_blank">QoS Scores</a-typography-link>
                    </div>
                </template>
            </a-alert>
            <a-alert
                type="error"
                :message="`Not enough tokens in the node wallet. Requires ${requiredStartTotalCNXAlert} CNX in total.`"
                class="top-alert"
                v-if="
          ((isNodeStoppedLike || isNodeInitializing) &&
          accountStatus.address !== '' &&
          hasLoadedWalletFundingState &&
          hasLoadedStakingAmount &&
          !startEnough())
        "
            >
                <template #action>
                    <a-button size="small" type="primary" :href="config.discord_link" target="_blank">Crynux Discord
                    </a-button>
                </template>
                <template #description>
                    Join the Crynux Discord to learn more about acquiring CNX tokens:
                    <a-typography-link :href="config.discord_link" target="_blank">{{ config.discord_link }}
                    </a-typography-link>
                </template>
            </a-alert>
            <a-alert
                type="error"
                :message="`Not enough tokens in the node wallet for gas. Requires at least ${gasFeeMinCNXAlert} CNX.`"
                class="top-alert"
                v-if="
          (isNodeRunning || isNodePaused) &&
          accountStatus.address !== '' &&
          hasLoadedWalletFundingState &&
          !gasEnough()
        "
            ></a-alert>
        </a-col>
    </a-row>
    <a-row ref="cardsRow1" :gutter="[16, 16]">
        <a-col
            :xs="{ span: 24, order: 1 }"
            :sm="{ span: 12, order: 1 }"
            :md="{ span: 12, order: 1 }"
            :lg="{ span: 8, order: 1 }"
            :xl="{ span: 7, offset: 1, order: 1 }"
            :xxl="{ span: 6, offset: 3, order: 1 }"
        >
            <a-card title="Node Status" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            v-if="isNodeRunning"
                        />
                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            status="exception"
                            v-if="isNodeError"
                        >
                        </a-progress>
                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            :stroke-color="'lightgray'"
                            v-if="(isNodePaused || isNodeStoppedLike)"
                        >
                            <template #format>
                <span style="font-size: 14px; color: lightgray">
                  <span v-if="isNodeInitializing"
                  >Preparing</span
                  >
                  <span v-if="isNodePaused">Paused</span>
                  <span v-if="isNodeStoppedLike">Stopped</span>
                </span>
                            </template>
                        </a-progress>
                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            :stroke-color="'cornflowerblue'"
                            v-if="(isNodePendingPause || isNodePendingStop || isNodeInitializing)"
                        >
                            <template #format>
                <span style="font-size: 14px; color: cornflowerblue">
                  <span v-if="isNodePendingPause"
                  >Pausing</span
                  >
                  <span v-if="isNodePendingStop"
                  >Stopping</span
                  >
                  <span v-if="isNodeInitializing"
                  >Preparing</span
                  >
                </span>
                            </template>
                        </a-progress>
                    </a-col>
                    <a-col :span="12">
                        <div class="node-op-btn" v-if="isNodeRunning">
                            <a-button
                                :icon="h(PauseCircleOutlined)"
                                @click="sendNodeAction('pause')"
                                :loading="isTxSending || nodeStatus.tx_status === nodeAPI.TX_STATUS_PENDING"
                                :disabled="!gasEnough()"
                            >Pause
                            </a-button
                            >
                        </div>
                        <div
                            class="node-op-btn"
                            style="margin-top: 8px"
                            v-if="isNodeRunning"
                        >
                            <a-button
                                :icon="h(LogoutOutlined)"
                                @click="sendNodeAction('stop')"
                                :loading="isTxSending || nodeStatus.tx_status === nodeAPI.TX_STATUS_PENDING"
                                :disabled="!gasEnough()"
                            >Stop
                            </a-button
                            >
                        </div>
                        <div class="node-op-btn" v-if="isNodeStoppedLike">
                            <a-button
                                type="primary"
                                :icon="h(PlayCircleOutlined)"
                                @click="sendNodeAction('start')"
                                :loading="isTxSending || nodeStatus.tx_status === nodeAPI.TX_STATUS_PENDING"
                                :disabled="!startEnough()"
                            >Start
                            </a-button
                            >
                        </div>
                        <div style="margin-top: 8px; text-align: left; margin-left: 8px" v-if="isNodeStoppedLike">
                            <a-typography-text type="secondary">
                                Staking: {{ stakingAmountText }}
                            </a-typography-text>
                            <a-button
                                type="text"
                                size="small"
                                :icon="h(EditOutlined)"
                                @click="systemStore.showSettingsModal = true"
                            />
                        </div>
                        <div class="node-op-btn" v-if="isNodePaused">
                            <a-button
                                type="primary"
                                :icon="h(PlayCircleOutlined)"
                                @click="sendNodeAction('resume')"
                                :loading="isTxSending || nodeStatus.tx_status === nodeAPI.TX_STATUS_PENDING"
                                :disabled="!gasEnough()"
                            >Resume
                            </a-button
                            >
                        </div>
                        <div class="node-op-btn" v-if="isNodeInitializing">
                            <a-button type="primary" :icon="h(PlayCircleOutlined)" disabled>Start</a-button>
                        </div>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>

        <a-col
            :xs="{ span: 24, order: 2 }"
            :sm="{ span: 12, order: 2 }"
            :md="{ span: 12, order: 2 }"
            :lg="{ span: 8, order: 2 }"
            :xl="{ span: 7, order: 2 }"
            :xxl="{ span: 6, order: 2 }"
        >
            <a-card title="Task Execution" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="8">
                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            :stroke-color="'cornflowerblue'"
                            v-if="taskStatus.status === 'idle'"
                        >
                            <template #format>
                                <span style="font-size: 14px; color: cornflowerblue">Idle</span>
                            </template>
                        </a-progress>

                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            status="success"
                            v-else-if="taskStatus.status === 'running'"
                        >
                            <template #format>
                                <span style="font-size: 14px">Running</span>
                            </template>
                        </a-progress>

                        <a-progress
                            type="circle"
                            :size="70"
                            :percent="100"
                            :stroke-color="'lightgray'"
                            v-else
                        >
                            <template #format>
                                <span style="font-size: 14px; color: lightgray">Stopped</span>
                            </template>
                        </a-progress>

                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="Today" :precision="0" :value="taskStatus.num_today"></a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="Total" :precision="0" :value="taskStatus.num_total"></a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>

        <a-col
            :xs="{ span: 24, order: 3 }"
            :sm="{ span: 12, order: 3 }"
            :md="{ span: 12, order: 3 }"
            :lg="{ span: 8, order: 3 }"
            :xl="{ span: 8, order: 3 }"
            :xxl="{ span: 6, order: 3 }"
        >
            <a-card title="Node Scores" :bordered="false" style="height: 100%; opacity: 0.9">
                <template #extra>
                    <a href="https://docs.crynux.io/system-design/task-dispatching#node-selection-probability" target="_blank">
                        <a-button type="text" :icon="h(QuestionCircleOutlined)" />
                    </a>
                </template>
                <a-row>
                    <a-col :span="8">
                        <a-statistic title="Staking">
                            <template #formatter>
                                <template v-if="isNodeJoined">
                                    <span class="score-value">{{ (nodeScores.staking * 100).toFixed(2) }}</span
                                    ><span class="score-percent">%</span>
                                </template>
                                <template v-else>
                                    <span class="score-value">-</span>
                                </template>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="QoS">
                            <template #formatter>
                                <template v-if="isNodeJoined">
                                    <span class="score-value">{{ (nodeScores.qos * 100).toFixed(2) }}</span
                                    ><span class="score-percent">%</span>
                                </template>
                                <template v-else>
                                    <span class="score-value">-</span>
                                </template>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="Prob Weight">
                            <template #formatter>
                                <template v-if="isNodeJoined">
                                    <span class="score-value">{{ (nodeScores.prob_weight * 200).toFixed(2) }}</span
                                    ><span class="score-percent">%</span>
                                </template>
                                <template v-else>
                                    <span class="score-value">-</span>
                                </template>
                            </template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col
            :xs="{ span: 24, order: 5 }"
            :sm="{ span: 24, order: 5 }"
            :md="{ span: 24, order: 5 }"
            :lg="{ span: 14, order: 4 }"
            :xl="{ span: 14, offset: 1, order: 4 }"
            :xxl="{ span: 10, offset: 3, order: 4 }"
        >
            <a-card title="Node Wallet" :bordered="false" style="height: 100%; opacity: 0.9">
                <template #extra>
                    <edit-account
                        ref="accountEditor"
                        :account-status="accountStatus"
                        @private-key-updated="privateKeyUpdated"
                    ></edit-account>
                </template>
                <a-row>
                    <a-col :span="8">
                        <a-tooltip>
                            <template #title>{{ accountStatus.address }}</template>
                            <a-statistic title="Address" class="wallet-address">
                                <template #formatter>
                                    <span>{{ shortAddress }}</span>
                                    <a-button @click="copyText(accountStatus.address)" style="margin-left: 8px">
                                        <template #icon>
                                            <CopyOutlined />
                                        </template>
                                    </a-button>
                                </template>
                            </a-statistic>
                        </a-tooltip>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="CNX Balance" class="wallet-value" >
                            <template #formatter>
                                <a-typography-text>{{ accountBalance }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="CNX Staked" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ accountStaked }}</a-typography-text>
                                <a-button @click="systemStore.showSettingsModal = true" style="margin-left: 8px">
                                    <template #icon>
                                        <EditOutlined />
                                    </template>
                                </a-button>
                            </template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col
            :xs="{ span: 24, order: 4 }"
            :sm="{ span: 12, order: 4 }"
            :md="{ span: 12, order: 4 }"
            :lg="{ span: 10, order: 5 }"
            :xl="{ span: 8, order: 5 }"
            :xxl="{ span: 8, order: 5 }"
        >
            <a-card title="Relay Account" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-statistic title="CNX Balance" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ relayBalance }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="12">
                        <a-statistic title="Action" class="wallet-value">
                            <template #formatter>
                                <a-button
                                    type="default"
                                    size="small"
                                    :disabled="accountStatus.address === ''"
                                    @click="showWithdrawModal = true"
                                >
                                    Withdraw
                                </a-button>
                            </template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
    </a-row>
    <a-row
        ref="cardsRowDelegators"
        v-if="accountStatus.delegator_share !== 0"
        :gutter="[16, 16]"
        style="margin-top: 16px"
    >
        <a-col
            :xs="{ span: 24 }"
            :sm="{ span: 12 }"
            :md="{ span: 12 }"
            :lg="{ span: 10 }"
            :xl="{ span: 8, offset: 1 }"
            :xxl="{ span: 8, offset: 3 }"
        >
            <a-card title="Delegator Rewards" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-statistic title="CNX Today" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ delegatorEarningsToday }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="12">
                        <a-statistic title="CNX Total" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ delegatorEarningsTotal }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col
            :xs="{ span: 24 }"
            :sm="{ span: 12 }"
            :md="{ span: 12 }"
            :lg="{ span: 14 }"
            :xl="{ span: 14 }"
            :xxl="{ span: 10 }"
        >
            <a-card title="Delegators" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="8">
                        <a-statistic title="Delegator Share" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ accountStatus.delegator_share }}%</a-typography-text>
                                <a-button @click="systemStore.showSettingsModal = true" style="margin-left: 8px">
                                    <template #icon>
                                        <EditOutlined />
                                    </template>
                                </a-button>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="Delegators" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ accountStatus.delegator_num }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="8">
                        <a-statistic title="CNX Staked" class="wallet-value">
                            <template #formatter>
                                <a-typography-text>{{ delegatorStaked }}</a-typography-text>
                            </template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
    </a-row>
    <a-row ref="cardsRow2" :gutter="[16, 16]" style="margin-top: 16px">
        <a-col
            :xs="{ span: 24, offset: 0 }"
            :sm="{ span: 16, offset: 0 }"
            :md="{ span: 16, offset: 0 }"
            :lg="{ span: 9, offset: 0 }"
            :xl="{ span: 7, offset: 1 }"
            :xxl="{ span: 6, offset: 3 }"
        >
            <a-card title="GPU" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="8">
                        <a-progress type="dashboard" :size="80" :percent="getPercent(systemInfo.gpu.usage)" />
                    </a-col>
                    <a-col :span="16">
                        <a-row>
                            <a-col :span="24">
                                <a-statistic :value="systemInfo.gpu.model" :value-style="{ 'font-size': '14px' }">
                                    <template #title><span style="font-size: 12px">Card Model</span></template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                        <a-row style="margin-top: 12px">
                            <a-col :span="12">
                                <a-statistic
                                    :value="systemInfo.gpu.vram_used_mb"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">VRAM Used</span></template>
                                    <template #suffix>MB</template>
                                </a-statistic>
                            </a-col>
                            <a-col :span="12">
                                <a-statistic
                                    :value="systemInfo.gpu.vram_total_mb"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">VRAM Total</span></template>
                                    <template #suffix>MB</template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col :xs="12" :sm="8" :md="8" :lg="5" :xxl="4">
            <a-card title="CPU" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-progress type="dashboard" :size="80" :percent="getPercent(systemInfo.cpu.usage)" />
                    </a-col>
                    <a-col :span="12">
                        <a-row>
                            <a-col :span="24">
                                <a-statistic
                                    :value="systemInfo.cpu.num_cores"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">Num of Cores</span></template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                        <a-row style="margin-top: 12px">
                            <a-col :span="24">
                                <a-statistic
                                    :value="systemInfo.cpu.frequency_mhz"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">Frequency</span></template>
                                    <template #suffix>MHz</template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col :xs="12" :sm="12" :md="12" :lg="5" :xxl="4">
            <a-card title="Memory" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-progress
                            type="dashboard"
                            :size="80"
                            :percent="
                getPercent(Math.round(
                  ((systemInfo.memory.total_mb - systemInfo.memory.available_mb) /
                    systemInfo.memory.total_mb) *
                    100
                ))
              "
                        />
                    </a-col>
                    <a-col :span="12">
                        <a-row>
                            <a-col :span="24">
                                <a-statistic
                                    :value="systemInfo.memory.total_mb - systemInfo.memory.available_mb"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">RAM Used</span></template>
                                    <template #suffix>MB</template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                        <a-row style="margin-top: 12px">
                            <a-col :span="24">
                                <a-statistic
                                    :value="systemInfo.memory.total_mb"
                                    :value-style="{ 'font-size': '14px' }"
                                >
                                    <template #title><span style="font-size: 12px">RAM Total</span></template>
                                    <template #suffix>MB</template>
                                </a-statistic>
                            </a-col>
                        </a-row>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
        <a-col :xs="12" :sm="12" :md="12" :lg="5" :xxl="4">
            <a-card title="Disk" :bordered="false" style="height: 100%; opacity: 0.9">
                <a-row>
                    <a-col :span="12">
                        <a-statistic
                            :value="hfModelsFormatted.value"
                            :value-style="{ 'font-size': '14px' }"
                        >
                            <template #title><span style="font-size: 12px">HF Models</span></template>
                            <template #suffix>{{ hfModelsFormatted.unit }}</template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="12">
                        <a-statistic
                            :value="externalModelsFormatted.value"
                            :value-style="{ 'font-size': '14px' }"
                        >
                            <template #title><span style="font-size: 12px">External Models</span></template>
                            <template #suffix>{{ externalModelsFormatted.unit }}</template>
                        </a-statistic>
                    </a-col>
                </a-row>
                <a-row style="margin-top: 12px">
                    <a-col :span="12">
                        <a-statistic :value="logsFormatted.value" :value-style="{ 'font-size': '14px' }">
                            <template #title><span style="font-size: 12px">Logs</span></template>
                            <template #suffix>{{ logsFormatted.unit }}</template>
                        </a-statistic>
                    </a-col>
                    <a-col :span="12">
                        <a-statistic :value="tempFilesFormatted.value" :value-style="{ 'font-size': '14px' }">
                            <template #title><span style="font-size: 12px">Temp Files</span></template>
                            <template #suffix>{{ tempFilesFormatted.unit }}</template>
                        </a-statistic>
                    </a-col>
                </a-row>
            </a-card>
        </a-col>
    </a-row>
    <div id="bottom-bar" :class="{'bottom-bar': true, 'fixed-bottom-bar': fixedBottomBar}">
        <a-row v-if="screens.xl" align="middle" style="height: 100%">
            <a-col :span="17">
                <a-space class="footer-links" align="center">
                    <a-typography-link href="https://crynux.io" target="_blank">Home</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-link href="https://docs.crynux.io" target="_blank">Docs</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-link href="https://blog.crynux.io" target="_blank">Blog</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-link href="https://twitter.com/crynuxio" target="_blank">Twitter</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-link :href="config.discord_link" target="_blank">Discord</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-link href="https://portal.crynux.io" target="_blank">Portal</a-typography-link>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-text :style="{'color':'white'}">Node v{{ appVersion }}</a-typography-text>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <a-typography-text :style="{'color':'white'}">Runner v{{ runnerVersion }}</a-typography-text>
                    <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                    <!-- Place this tag where you want the button to render. -->
                    <github-button
                        href="https://github.com/crynux-network/crynux-node"
                        data-color-scheme="no-preference: light; light: light; dark: light;"
                        data-show-count="true" aria-label="Star Crynux Node on GitHub"
                        :style="{'position': 'relative', 'top': '4px'}"
                    >Star
                    </github-button>
                </a-space>
            </a-col>
            <a-col :span="7">
                <div class="footer-logo">
                    <img src="./logo-full-white.png" width="140" alt="Crynux logo"/>
                    <div class="network-on">ON</div>
                    <img :class="`${config.network_logo}-logo`" :src="`/${config.network_logo}.png`" width="120"
                         alt="Blockchain logo"/>
                </div>
            </a-col>
        </a-row>
        <a-row v-else-if="screens.lg" align="middle" style="height: 100%">
            <a-col :span="16">
                <div class="footer-links">
                    <a-space direction="vertical" align="start">
                        <a-space :wrap="true">
                            <a-typography-text :style="{'color':'white'}">Node v{{ appVersion }}</a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-text :style="{'color':'white'}">Runner v{{ runnerVersion
                                }}
                            </a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <github-button
                                href="https://github.com/crynux-network/crynux-node"
                                data-color-scheme="no-preference: light; light: light; dark: light;"
                                data-show-count="true" aria-label="Star Crynux Node on GitHub"
                                :style="{'position': 'relative', 'top': '4px'}"
                            >Star
                            </github-button>
                        </a-space>
                        <a-space :wrap="true">
                            <a-typography-link href="https://crynux.io" target="_blank">Home</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://docs.crynux.io" target="_blank">Docs</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://blog.crynux.io" target="_blank">Blog</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://twitter.com/crynuxio" target="_blank"
                            >Twitter
                            </a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link :href="config.discord_link" target="_blank"
                            >Discord
                            </a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://portal.crynux.io" target="_blank"
                            >Portal
                            </a-typography-link>
                        </a-space>
                    </a-space>
                </div>
            </a-col>
            <a-col :span="8">
                <div class="footer-logo">
                    <img src="./logo-full-white.png" width="140" alt="Crynux logo"/>
                    <div class="network-on">ON</div>
                    <img :class="`${config.network_logo}-logo`" :src="`/${config.network_logo}.png`" width="120"
                         alt="Network logo"/>
                </div>
            </a-col>
        </a-row>
        <a-row v-else-if="screens.md" align="middle" style="padding: 16px 0;">
            <a-col :span="12">
                <div class="footer-links">
                    <a-space direction="vertical" align="start">
                        <a-space :wrap="true">
                            <a-typography-text :style="{'color':'white'}">Node v{{ appVersion }}</a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-text :style="{'color':'white'}">Runner v{{ runnerVersion
                                }}
                            </a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <github-button
                                href="https://github.com/crynux-network/crynux-node"
                                data-color-scheme="no-preference: light; light: light; dark: light;"
                                data-show-count="true" aria-label="Star Crynux Node on GitHub"
                                :style="{'position': 'relative', 'top': '4px'}"
                            >Star
                            </github-button>
                        </a-space>
                        <a-space :wrap="true">
                            <a-typography-link href="https://crynux.io" target="_blank">Home</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://docs.crynux.io" target="_blank">Docs</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://blog.crynux.io" target="_blank">Blog</a-typography-link>
                        </a-space>
                        <a-space :wrap="true">
                            <a-typography-link href="https://twitter.com/crynuxio" target="_blank">Twitter</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link :href="config.discord_link" target="_blank">Discord</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://portal.crynux.io" target="_blank">Portal</a-typography-link>
                        </a-space>
                    </a-space>
                </div>
            </a-col>
            <a-col :span="12">
                <div class="footer-logo">
                    <img src="./logo-full-white.png" width="140" alt="Crynux logo"/>
                    <div class="network-on">ON</div>
                    <img :class="`${config.network_logo}-logo`" :src="`/${config.network_logo}.png`" width="120"
                         alt="Network logo"/>
                </div>
            </a-col>
        </a-row>

        <a-row v-else :gutter="[0, 8]" style="padding: 8px 0;">
            <a-col :span="24">
                <div class="footer-links">
                    <a-space direction="vertical" align="start">
                        <a-space :wrap="true">
                            <a-typography-text :style="{'color':'white'}">Node v{{ appVersion }}</a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-text :style="{'color':'white'}">Runner v{{ runnerVersion
                                }}
                            </a-typography-text>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <github-button
                                href="https://github.com/crynux-network/crynux-node"
                                data-color-scheme="no-preference: light; light: light; dark: light;"
                                data-show-count="true" aria-label="Star Crynux Node on GitHub"
                                :style="{'position': 'relative', 'top': '4px'}"
                            >Star
                            </github-button>
                        </a-space>
                        <a-space :wrap="true">
                            <a-typography-link href="https://crynux.io" target="_blank">Home</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://docs.crynux.io" target="_blank">Docs</a-typography-link>
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://blog.crynux.io" target="_blank">Blog</a-typography-link>
                        </a-space>
                        <a-space :wrap="true">
                            <a-typography-link href="https://twitter.com/crynuxio" target="_blank"
                            >Twitter
                            </a-typography-link
                            >
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link :href="config.discord_link" target="_blank"
                            >Discord
                            </a-typography-link
                            >
                            <span class="bottom-bar-divider">&nbsp;|&nbsp;</span>
                            <a-typography-link href="https://portal.crynux.io" target="_blank"
                            >Portal
                            </a-typography-link
                            >
                        </a-space>
                    </a-space>
                </div>
            </a-col>
            <a-col :span="24">
                <div class="footer-logo" style="float: left;margin-top: 24px">
                    <img src="./logo-full-white.png" width="140" alt="Crynux logo"/>
                    <div class="network-on">ON</div>
                    <img :class="`${config.network_logo}-logo`" :src="`/${config.network_logo}.png`" width="120"
                         alt="Network logo"/>
                </div>
            </a-col>
        </a-row>
    </div>

    <a-modal
        v-model:visible="showCnxTokenModal"
        title="Get CNX Tokens"
        :footer="null"
        @cancel="showCnxTokenModal = false"
    >
        <p>Join the Crynux Discord to learn more about acquiring CNX tokens.</p>
        <a-button type="primary" :href="config.discord_link" target="_blank">
            Join Crynux Discord
        </a-button>
    </a-modal>

    <a-modal
        v-model:visible="showWithdrawModal"
        title="Withdraw"
        :footer="null"
        @cancel="showWithdrawModal = false"
    >
        <div style="text-align: center; margin: 24px 0;">
            <p>Please proceed to the Crynux Portal for withdrawals.</p>
            <a-button
                type="primary"
                href="https://portal.crynux.io"
                target="_blank"
                style="margin-top: 24px;"
            >
                Crynux Portal
            </a-button>
        </div>
    </a-modal>

    <a-modal
        v-model:visible="systemStore.showSettingsModal"
        title="Settings"
        :mask-closable="false"
        ok-text="Save"
        :confirm-loading="isSaving"
        :ok-button-props="{ disabled: isSettingsSaveDisabled }"
        @ok="handleSettingsSave"
        @cancel="handleSettingsCancel"
    >
        <a-alert
            v-if="showSuccessAlert"
            description="Settings saved successfully. It may take a few moments for the changes to be reflected on the dashboard."
            type="success"
            show-icon
            style="margin-bottom: 24px;"
        />
        <a-alert
            v-if="!hasEnoughGas && (isStakingChanged || isDelegatorShareChanged)"
            :message="settingsInsufficientText"
            type="error"
            show-icon
            style="margin-bottom: 16px;"
        />
        <a-form layout="vertical">
            <a-form-item
                label="Staking Amount"
                :validate-status="isStakingAmountValid ? '' : 'error'"
                :help="stakingAmountHelp"
            >
                <a-input-number v-model:value="settingsInModal.staking_amount" prefix="CNX" style="width: 100%"/>
            </a-form-item>
            <a-form-item>
                <template #label>
                    Delegator Share
                    <a-tooltip placement="right">
                        <template #title>
                            <div style="max-width: 420px">
                                <ul style="padding-left: 16px; margin: 0;">
                                    <li>Allocate a portion of rewards to Delegators to attract more staking.</li>
                                    <li>With a non-zero Delegator Share, your node appears in the Portal's Stakeable Nodes list and users can stake to your node.</li>
                                    <li>A higher staking score leads to more tasks and more rewards.</li>
                                </ul>
                            </div>
                        </template>
                        <QuestionCircleOutlined style="margin-left: 6px; color: rgba(0, 0, 0, 0.45)" />
                    </a-tooltip>
                </template>
                <a-row>
                    <a-col :span="20">
                        <a-slider
                            v-model:value="settingsInModal.delegator_share"
                            :min="0"
                            :max="100"
                            :step="1"
                            :tooltip-formatter="value => `${value}%`"
                        />
                    </a-col>
                    <a-col :span="4" style="text-align: right;">
                        <a-typography-text>{{ settingsInModal.delegator_share }}%</a-typography-text>
                    </a-col>
                </a-row>
            </a-form-item>
            <a-form-item label="Task Error Reporting">
                <a-space direction="vertical">
                    <a-space>
                        <a-switch v-model:checked="settingsInModal.task_error_report_automatic" />
                        <a-typography-text>Automatically report task execution errors</a-typography-text>
                    </a-space>
                    <a-space>
                        <a-button
                            :loading="isFlushingTaskErrors"
                            :disabled="settings.pending_task_error_reports === 0"
                            @click="handleTaskErrorReportFlush"
                        >
                            Report saved errors now
                        </a-button>
                        <a-typography-text type="secondary">
                            {{ settings.pending_task_error_reports }} pending
                        </a-typography-text>
                    </a-space>
                    <a-alert
                        v-if="taskErrorFlushResult"
                        :type="taskErrorFlushResult.type"
                        :message="taskErrorFlushResult.message"
                        show-icon
                    />
                </a-space>
            </a-form-item>
        </a-form>
    </a-modal>

</template>

<style lang="stylus">
.ant-row
    margin-left 0 !important
    margin-right 0 !important
</style>
<style scoped lang="stylus">
.score-value
    font-size 24px
    line-height 32px
    color rgba(0, 0, 0, 0.85)

.score-percent
    font-size 14px
    line-height 22px
    color rgba(0, 0, 0, 0.45)
    margin-left 4px

.wallet-address span
    font-size 16px

.wallet-value a,
    .wallet-value span
    font-size 16px
    color black
    text-decoration none

.xs .wallet-value a,
.xs .wallet-value span,
.xs .wallet-address span
    font-size 16px

.wallet-value a:hover
    color #1677ff

.top-alert
    margin-bottom 16px

.fixed-bottom-bar.bottom-bar
    position fixed
    bottom 0
    left 0
    margin-top 0

.bottom-bar
    position relative
    width 100%
    min-height 60px
    padding 0 32px
    margin-top 68px

.xs .bottom-bar .bottom-bar-divider
    display none

.footer-links
    color #fff
    opacity 0.8
    line-height normal

    a
        color #fff
        white-space: nowrap

        &:hover
            text-decoration underline

.xs .footer-links
    line-height 24px

.footer-logo
    opacity 0.8
    float right

    img
        display inline-block

    .network-on
        display inline-block
        background-color #fff
        color #000
        padding 2px 4px
        border-radius 4px
        font-size 10px
        font-weight bold
        margin-left 4px
        margin-right 4px

    .base-logo
        margin-top -4px

.xs .top-row
    height 64px
.sm .top-row
    height 64px
.md .top-row
    height 64px
.lg .top-row
    height 64px
.xl .top-row
    height 80px
.xxl .top-row
    height 100px
</style>
