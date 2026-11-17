import { test, expect } from '@playwright/test';

test('sliders synchronize, reject invalid edits, stage policies, and generate real traffic', async ({ page }) => {
  const external = [];
  await page.route('**/*', route => {
    if (!route.request().url().startsWith('http://localhost:8080')) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  await page.goto('/');
  const overview = page.getByRole('slider', { name: 'Number of requests' });
  await overview.focus();
  await overview.press('Home');
  await overview.press('ArrowRight');
  await expect(page.locator('#overview-count')).toHaveValue('2');
  await page.locator('#overview-count').fill('17');
  await expect(overview).toHaveValue('17');
  await page.locator('#overview-count').fill('501');
  await expect(page.locator('#overview-count')).toHaveAttribute('aria-invalid', 'true');
  await expect(overview).toHaveValue('17');
  await overview.focus();
  await expect(page.locator('#overview-count')).toHaveValue('17');
  await page.getByRole('button', { name: 'Generate Requests', exact: true }).click();
  await expect(page.getByText(/Offered 17:/)).toBeVisible();

  await page.getByRole('button', { name: 'Configuration', exact: true }).click();
  await expect(page.locator('#config-rate')).toBeEnabled();
  let saves = 0;
  page.on('request', request => { if (request.method() === 'PUT' && request.url().endsWith('/api/config')) saves++; });
  await page.locator('#config-rate').fill('7');
  await expect(page.locator('#config-rate-range')).toHaveValue('7');
  await page.waitForTimeout(3200);
  expect(saves).toBe(0);
  await expect(page.locator('#config-rate')).toHaveValue('7');
  await page.getByRole('button', { name: 'Apply configuration' }).click();
  await expect(page.getByText('Policy saved and active')).toBeVisible();
  expect(saves).toBe(1);
  await page.locator('select').filter({ has: page.locator('option[value="sliding_window"]') }).selectOption('sliding_window');
  await page.locator('#config-window_seconds').fill('5');
  await expect(page.locator('#config-rate-range')).toHaveAttribute('min', '5');
  await expect(page.locator('#config-rate-range')).toHaveAttribute('step', '5');
  await expect(page.locator('#config-rate-range')).toHaveValue('35');
  await page.locator('#config-rate').fill('40');
  const saved = page.waitForRequest(r => r.method() === 'PUT' && r.url().endsWith('/api/config'));
  await page.getByRole('button', { name: 'Apply configuration' }).click();
  expect((await saved).postDataJSON()).toMatchObject({ rate: 8, window_seconds: 5 });
  await expect(page.getByText('Policy saved and active')).toBeVisible();
  await page.getByRole('button', { name: 'Traffic Inspector', exact: true }).click();
  await page.locator('#traffic-count').fill('11');
  await page.locator('#traffic-rate').fill('200');
  await page.getByRole('button', { name: 'Generate traffic', exact: true }).click();
  await expect(page.getByText(/Offered 11:/)).toBeVisible();
  await expect(page.locator('tbody')).toContainText('demo-client');
  await page.getByRole('button', { name: 'Fault Injection', exact: true }).click();
  await expect(page.locator('#fault-duration-range')).toBeDisabled();
  await expect(page.locator('#fault-latency-range')).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect(overview).toBeVisible();
  const box = await overview.boundingBox();
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(external).toEqual([]);
});
