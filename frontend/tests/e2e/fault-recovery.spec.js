import { test, expect } from '@playwright/test';

test('Fault History renders automatic expiry and idempotent manual restoration', async ({ page }) => {
  await page.goto('/');
  const settings = await (await page.request.get('/api/faults')).json();
  test.skip(!settings.enabled, 'Requires local FAULTS_ENABLED=true');
  try {
    const injection = await page.request.post('/api/faults', {
      data: { name: 'worker_unavailable', enabled: true, duration_seconds: 5 }
    });
    expect(injection.ok()).toBeTruthy();
    await page.getByRole('button', { name: 'Logs & Analytics', exact: true }).click();
    await page.getByRole('button', { name: 'Fault History', exact: true }).click();
    const rows = page.locator('tbody tr').filter({ hasText: 'worker_unavailable' });
    await expect(rows.first().getByText('EXPIRED', { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(rows.first().getByText('AUTO_RECOVERED', { exact: true })).toBeVisible();
    await expect(rows.first()).not.toContainText('In progress');
    const again = await page.request.post('/api/faults', {
      data: { name: 'worker_unavailable', duration_seconds: 30 }
    });
    expect(again.ok()).toBeTruthy();
    for (let i = 0; i < 2; i++) {
      const restore = await page.request.post('/api/faults', {
        data: { name: 'worker_unavailable', enabled: false }
      });
      expect(restore.ok()).toBeTruthy();
    }
    await page.getByRole('button', { name: 'Refresh dashboard data' }).click();
    await expect(rows).toHaveCount(2);
    await expect(rows.first().getByText('CLEARED', { exact: true })).toBeVisible();
    await expect(rows.first().getByText('MANUAL_RECOVERED', { exact: true })).toBeVisible();
    await expect(rows.first()).not.toContainText('In progress');
    await expect(rows.last().getByText('AUTO_RECOVERED', { exact: true })).toBeVisible();
  } finally {
    await page.request.delete('/api/faults');
  }
});
