import BaseAPI from '../base-api'
import V1Client from '@/api/v1/v1'
import config from '@/config.json'


class NodeAPI extends BaseAPI {
  constructor() {
    super()

    this.NODE_STATUS_INITIALIZING = 'initializing'
    this.NODE_STATUS_RUNNING = 'running'
    this.NODE_STATUS_PAUSED = 'paused'
    this.NODE_STATUS_STOPPED = 'stopped'
    this.NODE_STATUS_SLASHED = 'slashed'
    this.NODE_STATUS_KICKED_OUT = 'kicked_out'
    this.NODE_STATUS_ERROR = 'error'
    this.NODE_STATUS_PENDING_PAUSE = 'pending_pause'
    this.NODE_STATUS_PENDING_STOP = 'pending_stop'

    this.TX_STATUS_ERROR = 'error'
    this.TX_STATUS_PENDING = 'pending'
    this.TX_STATUS_NONE = ''

      this.setHttpClient(new V1Client(config.base_url))
  }

  getNodeStatus() {
    return this.getHttpClient().get('/node')
  }

  getRunnerVersion() {
    return this.getHttpClient().get('/node/runner/version')
  }

  getNodeScores() {
    return this.getHttpClient().get('/node/scores')
  }

  getQosTracing() {
    return this.getHttpClient().get('/node/qos/tracing')
  }

  sendNodeAction(action) {
    return this.getHttpClient().post('/node', {
      action: action
    })
  }
}

export default NodeAPI
