const SECRET_KEYS = [
  'AZURE_RT2_ENDPOINT',
  'AZURE_RT2_KEY',
  'AZURE_RT2_DEPLOYMENT',
  'AZURE_GPT55_ENDPOINT',
  'AZURE_GPT55_KEY',
  'AZURE_GPT55_DEPLOYMENT',
  'AZURE_MINI_ENDPOINT',
  'AZURE_MINI_KEY',
  'AZURE_MINI_DEPLOYMENT',
  'AZURE_STT_KEY',
  'AZURE_STT_REGION',
  'COMPOSIO_API_KEY',
  'COMPOSIO_USER_ID',
]

const RESPONSE_KEYS = {
  AZURE_RT2_ENDPOINT: 'azure_rt2_endpoint',
  AZURE_RT2_KEY: 'azure_rt2_key',
  AZURE_RT2_DEPLOYMENT: 'azure_rt2_deployment',
  AZURE_GPT55_ENDPOINT: 'azure_gpt55_endpoint',
  AZURE_GPT55_KEY: 'azure_gpt55_key',
  AZURE_GPT55_DEPLOYMENT: 'azure_gpt55_deployment',
  AZURE_MINI_ENDPOINT: 'azure_mini_endpoint',
  AZURE_MINI_KEY: 'azure_mini_key',
  AZURE_MINI_DEPLOYMENT: 'azure_mini_deployment',
  AZURE_STT_KEY: 'azure_stt_key',
  AZURE_STT_REGION: 'azure_stt_region',
  COMPOSIO_API_KEY: 'composio_api_key',
  COMPOSIO_USER_ID: 'composio_user_id',
}

export default {
  async fetch(request, env) {
    const auth = request.headers.get('Authorization')
    if (auth !== `Bearer ${env.WORKER_API_KEY}`) {
      return new Response('Unauthorized', { status: 401 })
    }

    const url = new URL(request.url)

    if (request.method === 'GET' && url.pathname === '/secrets') {
      const secrets = {}
      for (const key of SECRET_KEYS) {
        if (env[key]) {
          secrets[RESPONSE_KEYS[key]] = env[key]
        }
      }
      return Response.json(secrets)
    }

    return new Response('Not found', { status: 404 })
  },
}
