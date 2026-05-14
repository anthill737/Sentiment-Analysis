import type { Page } from '@playwright/test'

export const PASSWORD = 'playwright-test'

export async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForSelector('input[type="password"]', { timeout: 8000 })
  await page.fill('input[type="password"]', PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForSelector('form', { timeout: 8000 })
}

export async function waitForIdle(
  page: Page,
  timeoutMs = 120_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const resp = await page.request.get('/api/jobs')
      if (resp.ok()) {
        const jobs = (await resp.json()) as Array<{ status: string }>
        if (!jobs.some((j) => j.status === 'running')) return
      }
    } catch {
      // server may be temporarily unavailable — keep retrying
    }
    await page.waitForTimeout(500)
  }
  throw new Error(`Server did not become idle within ${timeoutMs}ms`)
}

export async function submitJob(
  page: Page,
  subject: string,
): Promise<string> {
  await page.goto('/')
  await page.waitForSelector('form', { timeout: 8000 })
  await page.fill('textarea#subject', subject)
  await page.click('button[type="submit"]')
  await page.waitForURL('**/jobs/**', { timeout: 15_000 })
  return page.url().split('/jobs/')[1]
}
