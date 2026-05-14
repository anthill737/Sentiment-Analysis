import { test, expect } from '@playwright/test'
import { signIn, waitForIdle, submitJob } from './helpers'

// playwright.config.ts sets SA_RUNNER_MOCK_STEP_DELAY_MS=800.
// Pipeline timing with 800ms step delay:
//   Plan  (1 step):  ~800ms
//   Gather (concurrent, 3 lines each):  ~800ms
//   Analyze (7 sequential steps):  7 × 800ms ≈ 5.6s
//   Render (2 steps: render_report.js + word_convert):  2 × 800ms ≈ 1.6s

test.describe('Render Phase', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
    await waitForIdle(page)
  })

  test(
    'AC1+AC2: render node glows during Render phase; Phase Card is visible with elapsed time',
    async ({ page }) => {
      await submitJob(page, 'Render Phase E2E Test - Active')

      // -----------------------------------------------------------------------
      // AC1: Phase Timeline's render node shows as active while render is running.
      // The first three nodes must show as completed at this point.
      // -----------------------------------------------------------------------
      const renderNode = page.locator('[data-testid="phase-node-render"]')
      await expect(renderNode).toHaveAttribute('data-phase-state', 'active', {
        timeout: 60_000,
      })

      // The active node's inner div must carry the Tailwind animate-pulse class
      const renderNodeInner = renderNode.locator('> div').first()
      const classes = await renderNodeInner.getAttribute('class')
      expect(classes).toContain('animate-pulse')

      // Preceding nodes must be completed, not active
      for (const phase of ['plan', 'gather', 'analyze']) {
        const node = page.locator(`[data-testid="phase-node-${phase}"]`)
        await expect(node).toHaveAttribute('data-phase-state', 'completed', {
          timeout: 2000,
        })
      }

      // -----------------------------------------------------------------------
      // AC2: A Phase Card for Render is visible with the step label and
      //      a running elapsed time counter.
      // -----------------------------------------------------------------------
      const renderCard = page.locator('[data-testid="render-card"]')
      await expect(renderCard).toBeVisible({ timeout: 4000 })

      // Sub-step label must be present (Rendering DOCX… or Converting to PDF…)
      const subStep = page.locator('[data-testid="render-substep"]')
      await expect(subStep).toBeVisible({ timeout: 4000 })
      const subStepText = await subStep.innerText()
      expect(
        subStepText.includes('Rendering DOCX') || subStepText.includes('Converting to PDF'),
      ).toBe(true)

      // Elapsed timer must appear within a couple of seconds
      const elapsed = page.locator('[data-testid="render-elapsed"]')
      await expect(elapsed).toBeVisible({ timeout: 5000 })
    },
  )

  test(
    'AC3+AC4: render card shows checkmark on completion; all four timeline nodes are completed',
    async ({ page }) => {
      await submitJob(page, 'Render Phase E2E Test - Complete')

      // -----------------------------------------------------------------------
      // AC3: When Render completes, the Phase Card shows a "Done" completion
      //      indicator with a Framer Motion animated checkmark and the elapsed
      //      time stops incrementing.
      // -----------------------------------------------------------------------
      const renderDone = page.locator('[data-testid="render-done"]')
      await expect(renderDone).toBeVisible({ timeout: 60_000 })

      // Elapsed should be frozen — read twice 2 seconds apart and confirm no change
      const elapsed = page.locator('[data-testid="render-elapsed"]')
      await expect(elapsed).toBeVisible({ timeout: 2000 })
      const t1 = await elapsed.innerText()
      await page.waitForTimeout(2000)
      const t2 = await elapsed.innerText()
      expect(t1).toBe(t2)

      // -----------------------------------------------------------------------
      // AC4: All four Phase Timeline nodes show as completed once the Job is done.
      // -----------------------------------------------------------------------
      for (const phase of ['plan', 'gather', 'analyze', 'render']) {
        const node = page.locator(`[data-testid="phase-node-${phase}"]`)
        await expect(node).toHaveAttribute('data-phase-state', 'completed', {
          timeout: 5000,
        })
      }

      // The Results View must also appear alongside the render completion card
      const resultsView = page.locator('[data-testid="results-view"]')
      await expect(resultsView).toBeVisible({ timeout: 5000 })
    },
  )
})

// ---------------------------------------------------------------------------
// P4-T7: Results View, Downloads, and History after a full mocked Render run
// ---------------------------------------------------------------------------

test.describe('Render Phase — Results View, Downloads, and History', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
    await waitForIdle(page)
  })

  test(
    'AC1: full mocked run shows Results View with Verdict Pill, download buttons, angle chart, and pursue/pass cards',
    async ({ page }) => {
      await submitJob(page, 'P4-T7 AC1 Results View Full Check')

      const resultsView = page.locator('[data-testid="results-view"]')
      await expect(resultsView).toBeVisible({ timeout: 60_000 })

      // Verdict Pill
      await expect(page.locator('[data-testid="verdict-pill"]')).toBeVisible({
        timeout: 5_000,
      })

      // Both download buttons
      await expect(page.locator('[data-testid="download-pdf-btn"]')).toBeVisible({
        timeout: 5_000,
      })
      await expect(page.locator('[data-testid="download-docx-btn"]')).toBeVisible({
        timeout: 5_000,
      })

      // Angle-score chart
      await expect(page.locator('[data-testid="angle-score-chart"]')).toBeVisible({
        timeout: 5_000,
      })

      // At least one pursue reason card and one pass reason card
      await expect(page.locator('[data-testid="pursue-reason-0"]')).toBeVisible({
        timeout: 5_000,
      })
      await expect(page.locator('[data-testid="pass-reason-0"]')).toBeVisible({
        timeout: 5_000,
      })
    },
  )

  test(
    'AC2: Download PDF response has Content-Disposition attachment header and non-empty body',
    async ({ page }) => {
      const jobId = await submitJob(page, 'P4-T7 AC2 Download PDF Test')

      await expect(page.locator('[data-testid="results-view"]')).toBeVisible({
        timeout: 60_000,
      })

      // Verify the download button is present before exercising the endpoint
      await expect(page.locator('[data-testid="download-pdf-btn"]')).toBeVisible()

      // Fetch the PDF using the page's auth cookie context
      const resp = await page.request.get(`/api/jobs/${jobId}/download/pdf`)
      expect(resp.status()).toBe(200)

      const disposition = resp.headers()['content-disposition']
      expect(disposition).toBeTruthy()
      expect(disposition.toLowerCase()).toContain('attachment')

      const body = await resp.body()
      expect(body.byteLength).toBeGreaterThan(0)
    },
  )

  test(
    'AC3: /history card shows verdict, score, and working download links for a completed job',
    async ({ page }) => {
      const jobId = await submitJob(page, 'P4-T7 AC3 History Check Test')

      // Wait for the full mock pipeline to complete and results view to appear
      await expect(page.locator('[data-testid="results-view"]')).toBeVisible({
        timeout: 60_000,
      })

      // Navigate to /history
      await page.goto('/history')
      await page.waitForSelector(`[data-testid="history-card-${jobId}"]`, {
        timeout: 8_000,
      })

      const card = page.locator(`[data-testid="history-card-${jobId}"]`)
      await expect(card).toBeVisible({ timeout: 5_000 })

      // Card text must contain the verdict label and numeric score from executive.json fixture
      const cardText = await card.innerText()
      expect(cardText).toContain('Worth Exploring')
      expect(cardText).toMatch(/7\.9/)

      // Download links are visible on the completed job's History card
      const pdfLink = card.locator(`[data-testid="history-download-pdf-${jobId}"]`)
      const docxLink = card.locator(`[data-testid="history-download-docx-${jobId}"]`)
      await expect(pdfLink).toBeVisible({ timeout: 5_000 })
      await expect(docxLink).toBeVisible({ timeout: 5_000 })

      // PDF download link delivers a non-empty file
      const pdfResp = await page.request.get(`/api/jobs/${jobId}/download/pdf`)
      expect(pdfResp.status()).toBe(200)
      const pdfBody = await pdfResp.body()
      expect(pdfBody.byteLength).toBeGreaterThan(0)
    },
  )
})
