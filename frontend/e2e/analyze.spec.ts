import { test, expect } from '@playwright/test'
import { signIn, waitForIdle, submitJob } from './helpers'

// playwright.config.ts sets SA_RUNNER_MOCK_STEP_DELAY_MS=800.
// Pipeline timing with 800ms step delay:
//   Plan  (1 step):  ~800ms
//   Gather (concurrent, 3 lines each):  ~800ms
//   Analyze (7 sequential steps):  7 × 800ms ≈ 5.6s
//     sections step (step 4) starts at ~3 × 800ms = 2.4s into Analyze
//     sections emits 3 lines with ~267ms between them
//   Render (2 steps):  ~1.6s

test.describe('Analyze Phase', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
    await waitForIdle(page)
  })

  test(
    'full mocked Analyze run: Phase Timeline active, sections live text changes, and all 7 Phase Cards visible',
    async ({ page }) => {
      await submitJob(page, 'Analyze Phase E2E Test')

      // -----------------------------------------------------------------------
      // Assertion 1: Phase Timeline shows Analyze as the active phase
      // -----------------------------------------------------------------------
      const analyzeNode = page.locator('[data-testid="phase-node-analyze"]')
      await expect(analyzeNode).toHaveAttribute('data-phase-state', 'active', {
        timeout: 40_000,
      })

      // -----------------------------------------------------------------------
      // Assertion 2: write_sections Phase Card shows live section text that changes
      //
      // The sections step (step 4 of 7) emits 3 progress lines with ~267ms between:
      //   [sections] [1/3] writing 'Idea framing & hypotheses'  ← card appears
      //   [sections] [2/3] writing 'Demand validation'          ← text changes here
      //   [sections] [3/3] writing 'Competitive landscape'      ← sections→done, element gone
      //
      // waitForFunction captures the changed text atomically before the element
      // can disappear, avoiding a race condition on the 267ms window.
      // -----------------------------------------------------------------------
      const sectionsProgress = page.locator('[data-testid="analyze-sections-progress"]')
      await expect(sectionsProgress).toBeVisible({ timeout: 30_000 })

      const firstSectionsText = await sectionsProgress.innerText()
      expect(firstSectionsText).toBeTruthy()

      // Wait for text to change and capture the new text as the return value.
      // The predicate returns null (falsy → keep polling) while text is unchanged
      // and returns the new text string (truthy → resolve) once text changes.
      const changedTextHandle = await page.waitForFunction(
        (initText: string) => {
          const el = document.querySelector('[data-testid="analyze-sections-progress"]')
          if (!el || el.textContent === initText) return null
          return el.textContent
        },
        firstSectionsText,
        { timeout: 4_000, polling: 50 },
      )
      const changedSectionsText = await changedTextHandle.jsonValue()
      expect(changedSectionsText).toBeTruthy()
      expect(changedSectionsText).not.toBe(firstSectionsText)

      // -----------------------------------------------------------------------
      // Assertion 3: All 7 Analyze Phase Cards are visible in the Dashboard View
      //
      // Cards slide in as each step starts. By the time assemble (step 7) starts,
      // all 6 preceding cards are already in the DOM showing Done state.
      // waitForFunction is the assertion: it throws on timeout if all 7 are never
      // simultaneously present. This window is ~800ms before the Render phase
      // takes over and the analyze-cards container exits.
      // -----------------------------------------------------------------------
      await page.waitForFunction(
        () => {
          const steps = [
            'extract',
            'cluster',
            'score',
            'sections',
            'executive',
            'charts',
            'assemble',
          ]
          return steps.every(
            (step) =>
              !!document.querySelector(
                `[data-testid="analyze-step-card-${step}"]`,
              ),
          )
        },
        { timeout: 30_000 },
      )

      // Phase Timeline still in analyze range at this point (active or just completed)
      const phaseState = await analyzeNode.getAttribute('data-phase-state')
      expect(['active', 'completed', 'done']).toContain(phaseState)
    },
  )
})
