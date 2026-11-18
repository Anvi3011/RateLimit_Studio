import { test, expect } from '@playwright/test';

async function openConfiguration(page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Configuration', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Apply configuration', exact: true })).toBeEnabled();
}

for (const algorithm of ['token_bucket', 'sliding_window']) {
  test(`${algorithm}: 100 browser-paced HTTP decisions reach Redis, RabbitMQ and PostgreSQL`, async ({ page }) => {
    test.setTimeout(60_000);
    await openConfiguration(page);
    await page.locator('select').filter({ has: page.locator('option[value="token_bucket"]') }).selectOption(algorithm);
    await page.getByRole('spinbutton', { name: 'Maximum requests / second', exact: true }).fill('2');
    if (algorithm === 'token_bucket')
      await page.getByRole('spinbutton', { name: 'Burst capacity', exact: true }).fill('5');
    else
      await page.getByRole('spinbutton', { name: 'Window duration (seconds)', exact: true }).fill('1');
    await page.getByRole('button', { name: 'Apply configuration', exact: true }).click();
    await expect(page.getByText('Policy saved and active')).toBeVisible();
    await page.locator('#traffic-count').fill('100');
    await page.locator('#traffic-rate').fill('50');
    const client = `slider-${algorithm}-${Date.now()}`;
    await page.locator('#traffic-client').fill(client);
    const statuses = [];
    const submittedAt = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/requests/test') submittedAt.push(Date.now());
      expect(new URL(request.url()).pathname).not.toBe('/api/traffic/generate');
    });
    page.on('response', (response) => {
      if (new URL(response.url()).pathname === '/api/requests/test') statuses.push(response.status());
    });
    await page.getByRole('button', { name: 'Generate traffic', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Generate traffic', exact: true })).toBeDisabled();
    await expect(page.locator('.notice')).toContainText('Offered 100:', { timeout: 30_000 });
    await expect(page.locator('.notice')).toContainText('Target 50 RPS; observed');
    await expect(page.locator('.notice')).toContainText('0 errors');
    expect(statuses).toHaveLength(100);
    expect(statuses.filter((status) => status === 200).length).toBeGreaterThan(0);
    expect(statuses.filter((status) => status === 429).length).toBeGreaterThan(0);
    expect(submittedAt.at(-1) - submittedAt[0]).toBeGreaterThanOrEqual(1800);

    await expect.poll(async () => (await (await page.request.get(
      `/api/logs/requests?client_id=${encodeURIComponent(client)}&page_size=100`)).json()).total,
    { timeout: 15_000 }).toBe(100);
    const logs = await (await page.request.get(`/api/logs/requests?client_id=${encodeURIComponent(client)}&page_size=100`)).json();
    expect(logs.items.every((row) => row.algorithm === algorithm)).toBe(true);
    expect(logs.items.filter((row) => row.response_status === 429).length).toBeGreaterThan(0);
    const metrics = await (await page.request.get('/api/metrics')).json();
    expect(metrics.accepted + metrics.rejected).toBe(100);
    const health = await (await page.request.get('/api/health')).json();
    for (const component of ['database', 'redis', 'rabbitmq', 'publisher', 'worker'])
      expect(health[component]).toBe('healthy');
    await page.getByRole('button', { name: 'Overview', exact: true }).click();
    await expect(page.getByRole('img', { name: /Measured accepted, rejected and error request counts/ })).toBeVisible();
    await page.getByRole('button', { name: 'Traffic Inspector', exact: true }).click();
    await expect(page.locator('tbody')).toContainText(client);
    await page.getByRole('button', { name: 'Logs & Analytics', exact: true }).click();
    await expect(page.locator('tbody')).toContainText(client);
  });
}

test('a run cannot overlap quick traffic and can be cancelled with partial results', async ({ page }) => {
  await openConfiguration(page);
  await page.locator('#traffic-count').fill('100');
  await page.locator('#traffic-rate').fill('1');
  let attempted = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/requests/test') attempted++;
  });
  await page.getByRole('button', { name: 'Generate traffic', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel traffic', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send 10 requests', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Send 100 requests', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel traffic', exact: true }).click();
  await expect(page.locator('.notice')).toContainText('cancelled');
  expect(attempted).toBeLessThan(100);
  await expect(page.getByRole('button', { name: 'Send 10 requests', exact: true })).toBeEnabled();
  const atCancellation = attempted;
  await page.waitForTimeout(1200);
  expect(attempted).toBe(atCancellation);
});
