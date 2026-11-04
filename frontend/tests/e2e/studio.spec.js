import { test, expect } from '@playwright/test'

const token = process.env.ADMIN_TOKEN
const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8080'
let originalConfig
let originalFaults
let originalFaultTtl
let api

test.beforeAll(async ({ playwright }) => {
  if (!token) throw new Error('Set ADMIN_TOKEN to the local Compose admin token')
  api = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { 'X-Admin-Token': token } })
  const response = await api.get('/api/config')
  expect(response.ok()).toBeTruthy()
  originalConfig = await response.json()
  const faultSnapshot = await (await api.get('/api/faults')).json()
  originalFaults = faultSnapshot.active || {}
  originalFaultTtl = faultSnapshot.ttl_seconds || {}
})

test.afterAll(async () => {
  if (!api) return
  await api.delete('/api/faults')
  for (const [name, value] of Object.entries(originalFaults || {})) {
    await api.post('/api/faults', { data: { name, enabled: true, value: Number(value) || 250, duration_seconds: Math.max(1, Math.min(300, originalFaultTtl?.[name] || 60)) } })
  }
  if (originalConfig) await api.put('/api/config', { data: originalConfig })
  await api.dispose()
})

test('edits and persists policy, generates traffic, inspects decisions, and controls recoverable faults', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()

  await page.getByRole('button', { name: 'Configuration' }).click()
  await expect(page.getByRole('button', { name: 'Apply configuration' })).toBeEnabled()
  await page.getByLabel('Algorithm').selectOption('token_bucket')
  const rate = page.getByLabel('Maximum requests / second')
  await rate.fill('3')
  await page.waitForTimeout(3500) // Cross the three-second background poll while the form is dirty.
  await expect(rate).toHaveValue('3')
  await page.getByLabel('Admin token').fill(token)
  await page.getByRole('button', { name: 'Apply configuration' }).click()
  await expect(page.getByText('Policy saved and active')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh dashboard data' }).click()
  await expect(rate).toHaveValue('3')
  await page.reload()
  await page.getByRole('button', { name: 'Configuration' }).click()
  await expect(page.getByLabel('Maximum requests / second')).toHaveValue('3')

  await page.getByLabel('Requests', { exact: true }).fill('8')
  await page.getByLabel('Client identifier').fill(`e2e-${Date.now()}`)
  await page.getByLabel('Incoming requests / second').fill('20')
  await page.getByRole('button', { name: 'Generate traffic' }).click()
  await expect(page.getByText(/Offered 8:/)).toBeVisible()
  const sampled = await api.get('/api/metrics/timeseries?seconds=60&bucket_seconds=5')
  expect((await sampled.json()).points.some(point => point.requests > 0)).toBeTruthy()
  await page.getByRole('button', { name: 'Overview' }).click()
  await expect(page.getByRole('img', { name: /Measured accepted, rejected and error request counts/ })).toBeVisible()
  await page.getByRole('button', { name: 'Traffic Inspector' }).click()
  await expect(page.getByRole('heading', { name: 'Request decisions' })).toBeVisible()
  await expect(page.locator('tbody tr').first()).toBeVisible()
  await expect(page.locator('tbody')).toContainText('e2e-')

  await page.getByRole('button', { name: 'Fault Injection' }).click()
  await page.getByLabel('Admin token').fill(token)
  await page.getByLabel('Auto-expire after (seconds)').fill('20')
  const degradedRow = page.locator('.faultrow').filter({ hasText: 'API degraded' })
  page.once('dialog', dialog => dialog.accept())
  await degradedRow.getByRole('button', { name: 'Inject' }).click()
  await expect(degradedRow).toContainText(/expires in/)
  await page.getByRole('button', { name: 'Restore all faults' }).click()
  await expect(degradedRow.getByRole('button', { name: 'Inject' })).toBeVisible()

  // The API delay exceeds the 3 second inner Nginx read timeout, producing an
  // observable client timeout/504. Its two-second Redis TTL then restores it.
  const timeoutFault = await api.post('/api/faults', { data: { name: 'network_timeout', enabled: true, duration_seconds: 2 } })
  expect(timeoutFault.ok()).toBeTruthy()
  let timedOut = false
  try {
    await api.post('/api/requests/test', { data: { client_id: `timeout-${Date.now()}` }, timeout: 1500 })
  } catch (error) {
    timedOut = /timeout/i.test(String(error))
  }
  expect(timedOut).toBeTruthy()
  await page.waitForTimeout(2600)
  const recovered = await api.get('/api/faults')
  expect((await recovered.json()).active.network_timeout).toBeUndefined()
  const afterRecovery = await api.post('/api/requests/test', { data: { client_id: `recovered-${Date.now()}` }, timeout: 8000 })
  expect([200, 429]).toContain(afterRecovery.status())
})
