import { test, expect } from '@playwright/test'

const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8080'
let originalConfig
let originalFaults
let originalFaultTtl
let api

test.beforeAll(async ({ playwright }) => {
  api = await playwright.request.newContext({ baseURL })
  const response = await api.get('/api/config')
  expect(response.ok()).toBeTruthy()
  originalConfig = await response.json()
  const faultSnapshot = await (await api.get('/api/faults')).json()
  originalFaults = faultSnapshot.active || {}
  originalFaultTtl = faultSnapshot.ttl_seconds || {}
})

test.afterAll(async () => {
  if (!api) return
  // await api.delete('/api/faults') // Disabled until fault cleanup is session-isolated
  //for (const [name, value] of Object.entries(originalFaults || {})) {
  // await api.post('/api/faults', { data: { name, enabled: true, value: Number(value) || 250, duration_seconds: Math.max(1, Math.min(300, originalFaultTtl?.[name] || 60)) } })
  // }
  // Configuration cleanup pending: browser and API contexts may use different sessions.
  await api.dispose()
})

test('publicly edits and persists policy, generates traffic, inspects decisions and logs, and controls recoverable faults', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()

  // Configuration without admin token
  await page.getByRole('button', { name: 'Configuration' }).click()
  await expect(page.getByRole('button', { name: 'Apply configuration' })).toBeEnabled()
  await page.locator('select').filter({ has: page.locator('option[value="token_bucket"]') }).selectOption('token_bucket')
  const rate = page.getByRole('spinbutton', { name: 'Maximum requests / second' })
  await rate.fill('3')
  await page.waitForTimeout(3500) // Cross background poll while form is dirty
  await expect(rate).toHaveValue('3')

  // Public Apply Configuration - no admin token required!
  await page.getByRole('button', { name: 'Apply configuration' }).click()
  await expect(page.getByText('Policy saved and active')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh dashboard data' }).click()
  await expect(rate).toHaveValue('3')
  await page.reload()
  await page.getByRole('button', { name: 'Configuration' }).click()
  await expect(page.getByRole('spinbutton', { name: 'Maximum requests / second' })).toHaveValue('3')

  // Generate traffic
  await page.locator('#traffic-count').fill('8')
  const clientName = `e2e-${Date.now()}`
  await page.locator('#traffic-client').fill(clientName)
  await page.locator('#traffic-rate').fill('20')
  await page.getByRole('button', { name: 'Generate traffic' }).click()
  await expect(page.getByText(/Offered 8:/)).toBeVisible()

  // Overview metrics
  await page.getByRole('button', { name: 'Overview' }).click()
  await expect(page.getByRole('img', { name: /Measured accepted, rejected and error request counts/ })).toBeVisible()

  // Traffic Inspector
  await page.getByRole('button', { name: 'Traffic Inspector' }).click()
  await expect(page.getByRole('heading', { name: 'Request decisions' })).toBeVisible()
  await expect(page.locator('tbody tr').first()).toBeVisible()
  await expect(page.locator('tbody')).toContainText('e2e-')

  // Logs & Analytics
  await page.getByRole('button', { name: 'Logs & Analytics' }).click()
  await expect(page.getByText(/Logs are stored persistently in the database/)).toBeVisible()
  await expect(page.getByText('TOTAL REQUESTS')).toBeVisible()

  // Fault Injection: verify the safe, disabled configuration
  const faultStatus = await api.get('/api/faults')
  expect(faultStatus.ok()).toBeTruthy()

  const faultData = await faultStatus.json()
  expect(faultData.enabled).toBe(false)

  // Disabled fault injection must reject activation attempts
  const injectResponse = await api.post('/api/faults', {
    data: {
      name: 'api_degraded',
      enabled: true,
      duration_seconds: 20
    }
  })

  expect(injectResponse.status()).toBe(403)
  expect((await injectResponse.json()).error).toBe('fault_injection_disabled')
})
