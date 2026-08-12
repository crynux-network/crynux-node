import BaseAPI from '../base-api'
import V1Client from '@/api/v1/v1'
import config from '@/config.json'

class AccountAPI extends BaseAPI {

    constructor(){
        super()
        this.setHttpClient(new V1Client(config.base_url))
    }

    getAccountInfo() {
        return this.getHttpClient().get('/account')
    }

    getVestings(page = 1, pageSize = 20) {
        return this.getHttpClient().get('/account/vesting/list', {
            params: {
                page,
                page_size: pageSize
            }
        })
    }

    updatePrivateKey(privateKey) {
        return this.getHttpClient().put('/account', {
            type: 'private_key',
            private_key: privateKey
        })
    }

    updateKeystore(keystore, passphrase) {
        return this.getHttpClient().put('/account', {
            type: 'keystore',
            keystore: keystore,
            passphrase: passphrase
        })
    }

    createAccount() {
        return this.getHttpClient().post('/account', {})
    }

    updateDelegatorShare(share) {
        return this.getHttpClient().post('/delegator/share', { share: share })
    }
}

export default AccountAPI
