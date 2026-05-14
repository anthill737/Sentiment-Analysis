import { test, expect } from '@playwright/test'
import { signIn, waitForIdle, submitJob } from './helpers'

// With SA_RUNNER_MOCK_STEP_DELAY_MS=800:
// Plan(1×800ms) + Gather(800ms concurrent) + Analyze(7×800ms=5.6s) + Render(2×800ms=1.6s) ≈ 9s total

test.describe('Job lifecycle', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
    await waitForIdle(page)
  })

  test('new-job form submission redirects to /jobs/:id', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('form', { timeout: 8000 })

    await page.fill('textarea#subject', 'E2E Redirect Test')
    await page.click('button[type="submit"]')

    await page.waitForURL('**/jobs/**', { timeout: 15_000 })
    expect(page.url()).toMatch(/\/jobs\/[0-9a-f-]{36}/)
  })

  test('Phase Timeline renders all four phase nodes and advances', async ({
    page,
  }) => {
    await submitJob(page, 'Phase Timeline Progression Test')

    // Phase Timeline must be visible
    await expect(
      page.locator('[data-testid="phase-timeline"]'),
    ).toBeVisible({ timeout: 10_000 })

    // All four phase nodes must be present
    for (const phase of ['plan', 'gather', 'analyze', 'render']) {
      await expect(
        page.locator(`[data-testid="phase-node-${phase}"]`),
      ).toBeVisible({ timeout: 5000 })
    }

    // Observe gather phase as active (plan has already completed)
    await expect(
      page.locator('[data-testid="phase-node-gather"]'),
    ).toHaveAttribute('data-phase-state', /active|done/, { timeout: 20_000 })

    // Observe analyze phase as active
    await expect(
      page.locator('[data-testid="phase-node-analyze"]'),
    ).toHaveAttribute('data-phase-state', /active|done/, { timeout: 30_000 })

    // Results view appears when job completes (all four phases traversed)
    await expect(
      page.locator('[data-testid="results-view"]'),
    ).toBeVisible({ timeout: 60_000 })
  })

  test('Results View renders verdict pill and angle-score bar chart', async ({
    page,
  }) => {
    await submitJob(page, 'Results View E2E Test')

    // Wait for the full mock pipeline to complete
    await expect(
      page.locator('[data-testid="results-view"]'),
    ).toBeVisible({ timeout: 60_000 })

    // Verdict Pill must be visible
    await expect(
      page.locator('[data-testid="verdict-pill"]'),
    ).toBeVisible({ timeout: 5000 })

    // Angle-score bar chart must be visible
    await expect(
      page.locator('[data-testid="angle-score-chart"]'),
    ).toBeVisible({ timeout: 5000 })

    // Six score bars (one per angle)
    await expect(
      page.locator('[data-testid="score-bar"]'),
    ).toHaveCount(6, { timeout: 5000 })
  })

  test('/history lists the completed job', async ({ page }) => {
    await submitJob(page, 'History Listing E2E Test')

    // Wait for job to complete
    await expect(
      page.locator('[data-testid="results-view"]'),
    ).toBeVisible({ timeout: 60_000 })

    // Navigate to /history
    await page.goto('/history')
    await page.waitForSelector('[data-testid^="history-card-"]', {
      timeout: 8000,
    })

    const cards = page.locator('[data-testid^="history-card-"]')
    expect(await cards.count()).toBeGreaterThan(0)

    // Most recent card should contain our subject
    const firstCardText = await cards.first().innerText()
    expect(firstCardText).toContain('History Listing E2E Test')
  })

  test('rejected second submission shows error banner', async ({ page }) => {
    // Submit first job and navigate away quickly (don't wait for completion)
    await page.goto('/')
    await page.waitForSelector('form', { timeout: 8000 })
    await page.fill('textarea#subject', 'First Job — conflict test')
    await page.click('button[type="submit"]')
    await page.waitForURL('**/jobs/**', { timeout: 15_000 })

    // Navigate back to / and immediately try to submit a second job
    await page.goto('/')
    await page.waitForSelector('form', { timeout: 8000 })
    await page.fill('textarea#subject', 'Second Job — should be rejected')
    await page.click('button[type="submit"]')

    // The form should show a "already running" error — not redirect to /jobs/:id
    await expect(
      page.locator('text=/already running/i'),
    ).toBeVisible({ timeout: 8000 })

    // URL must still be / (no redirect happened)
    expect(page.url()).not.toMatch(/\/jobs\/[0-9a-f-]/)
  })
})
