import { test, expect } from '@playwright/test';

async function configuration(page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Configuration', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Apply configuration', exact: true })).toBeEnabled();
}

test('configuration slider boundaries and numeric inputs stay synchronized without auto-saving', async ({ page }) => {
  await configuration(page);
  const before = await (await page.request.get('/api/config')).json();
  for (const [name, max, algorithm] of [
    ['Maximum requests / second', 100, 'token_bucket'],
    ['Burst capacity', 500, 'token_bucket'],
    ['Edge nodes', 10, 'token_bucket'],
    ['Window duration (seconds)', 60, 'sliding_window'],
  ]) {
    await page.locator('select').filter({ has: page.locator('option[value="token_bucket"]') }).selectOption(algorithm);
    const input = page.getByRole('spinbutton', { name, exact: true });
    const slider = page.getByRole('slider', { name: `${name} slider`, exact: true });
    await expect(slider).toHaveAttribute('max', String(max));
    await slider.press('End');
    await expect(input).toHaveValue(String(max));
    await slider.press('Home');
    await expect(input).toHaveValue('1');
    await input.fill('3');
    await expect(slider).toHaveValue('3');
  }
  const unchanged = await (await page.request.get('/api/config')).json();
  expect(unchanged).toEqual(before);
});

test('invalid drafts allow typing, block apply and normalize on blur', async ({ page }) => {
  await configuration(page);
  const input = page.getByRole('spinbutton', { name: 'Maximum requests / second', exact: true });
  const slider = page.getByRole('slider', { name: 'Maximum requests / second slider', exact: true });
  const apply = page.getByRole('button', { name: 'Apply configuration', exact: true });
  await input.fill('');
  await expect(input).toHaveValue('');
  await expect(apply).toBeDisabled();
  await expect(slider).toBeDisabled();
  await input.fill('0');
  await expect(apply).toBeDisabled();
  await input.press('Tab');
  await expect(input).toHaveValue('1');
  await expect(slider).toHaveValue('1');
  await input.fill('2.5');
  await expect(apply).toBeDisabled();
  await input.press('Tab');
  await expect(input).toHaveValue('3');
  await input.fill('100001');
  await input.press('Tab');
  await expect(input).toHaveValue('100000');
  await expect(slider).toHaveValue('100000');
});

test('custom API-supported numeric values remain usable and submit only on Apply', async ({ page }) => {
  await configuration(page);
  const input = page.getByRole('spinbutton', { name: 'Maximum requests / second', exact: true });
  const previous = (await (await page.request.get('/api/config')).json()).rate;
  await input.fill('321');
  await expect(page.getByRole('slider', { name: 'Maximum requests / second slider' })).toHaveValue('321');
  expect((await (await page.request.get('/api/config')).json()).rate).toBe(previous);
  await page.getByRole('button', { name: 'Apply configuration', exact: true }).click();
  await expect(page.getByText('Policy saved and active')).toBeVisible();
  expect((await (await page.request.get('/api/config')).json()).rate).toBe(321);
  await page.reload();
  await page.getByRole('button', { name: 'Configuration', exact: true }).click();
  await expect(input).toHaveValue('321');
  const slider = page.getByRole('slider', { name: 'Maximum requests / second slider' });
  await slider.press('Home');
  await expect(input).toHaveValue('1');
  await expect(slider).toHaveAttribute('max', '321');
});

test('algorithm-specific controls and traffic boundaries respect safety limits', async ({ page }) => {
  await configuration(page);
  const algorithm = page.locator('select').filter({ has: page.locator('option[value="token_bucket"]') });
  await expect(page.getByRole('slider', { name: 'Window duration (seconds) slider', exact: true })).toBeDisabled();
  await algorithm.selectOption('sliding_window');
  await expect(page.getByRole('slider', { name: 'Burst capacity slider', exact: true })).toBeDisabled();
  await expect(page.getByRole('slider', { name: 'Window duration (seconds) slider', exact: true })).toBeEnabled();
  const count = page.locator('#traffic-count');
  await expect(count).toHaveValue('25');
  const countSlider = page.getByRole('slider', { name: 'Requests slider', exact: true });
  await countSlider.press('Home');
  await expect(count).toHaveValue('1');
  await countSlider.press('End');
  await expect(count).toHaveValue('1000');
  await expect(page.getByRole('button', { name: 'Generate traffic', exact: true })).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('500');
  await count.fill('100');
  await expect(countSlider).toHaveValue('100');
  const rpsSlider = page.getByRole('slider', { name: 'Incoming requests / second slider', exact: true });
  await rpsSlider.press('Home');
  await expect(page.locator('#traffic-rate')).toHaveValue('1');
  await rpsSlider.press('End');
  await expect(page.locator('#traffic-rate')).toHaveValue('200');
  await page.locator('#traffic-rate').fill('50');
  await expect(rpsSlider).toHaveValue('50');
  await expect(page.getByText('Target: 50 RPS; actual throughput may be lower.')).toBeVisible();
});

test('applying one visitor configuration cannot change another visitor policy', async ({ page, browser }) => {
  await configuration(page);
  const second = await browser.newContext();
  try {
    const url = new URL('/api/config', page.url()).href;
    const otherBefore = await (await second.request.get(url)).json();
    await page.getByRole('spinbutton', { name: 'Maximum requests / second', exact: true }).fill('17');
    await page.getByRole('button', { name: 'Apply configuration', exact: true }).click();
    await expect(page.getByText('Policy saved and active')).toBeVisible();
    const otherAfter = await (await second.request.get(url)).json();
    expect(otherAfter.rate).toBe(otherBefore.rate);
    expect(otherAfter.policy_id).not.toBe((await (await page.request.get('/api/config')).json()).policy_id);
  } finally {
    await second.close();
  }
});

test('waits for the session cookie, preserves the active policy through unsaved edits and refresh', async ({ page }) => {
  let sessionFinished = false;
  const preSession = [];
  await page.route('**/api/session', async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 250));
    sessionFinished = true;
    await route.fulfill({ response });
  }, { times: 1 });
  page.on('request', (req) => {
    const path = new URL(req.url()).pathname;
    if (!sessionFinished && path.startsWith('/api/') && path !== '/api/session') preSession.push(path);
  });
  await configuration(page);
  expect(preSession).toEqual([]);
  const algorithm = page.locator('select').filter({ has: page.locator('option[value="token_bucket"]') });
  await algorithm.selectOption('sliding_window');
  await expect(page.getByText('Unsaved edits', { exact: true })).toBeVisible();
  await expect(page.getByText(/^Active policy: Token Bucket/)).toBeVisible();
  await algorithm.selectOption('token_bucket');
  await expect(page.getByText('Showing active policy', { exact: true })).toBeVisible();
  const backendSession = (await (await page.request.get('/api/session')).json()).session_id;
  await page.reload();
  expect((await (await page.request.get('/api/config')).json()).policy_id).toBe(backendSession);
});
