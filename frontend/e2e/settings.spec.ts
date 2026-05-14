import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

const PROVIDERS = ['anthropic', 'perplexity', 'xai', 'fmp'] as const

test.describe('Settings — API key management', () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page)
  })

  test('settings view shows all four providers', async ({ page }) => {
    await page.goto('/settings')
    for (const provider of PROVIDERS) {
      await expect(page.getByTestId(`provider-card-${provider}`)).toBeVisible({
        timeout: 5000,
      })
    }
  })

  // AC4: entering and saving an API key for each provider shows "configured" + Rotate button
  test('AC4: all four providers show configured with Rotate button after saving keys', async ({
    page,
  }) => {
    await page.goto('/settings')

    for (const provider of PROVIDERS) {
      const card = page.getByTestId(`provider-card-${provider}`)

      // If a rotate input is already open for this card, cancel it first
      const cancelBtn = card.locator('button', { hasText: 'Cancel' })
      if (await cancelBtn.isVisible()) {
        await cancelBtn.click()
      }

      await card.locator('button', { hasText: 'Rotate' }).click()
      const keyInput = page.locator('input[placeholder*="Paste new API key"]')
      await expect(keyInput).toBeVisible({ timeout: 3000 })
      await keyInput.fill(`pw-test-key-${provider}`)
      await card.locator('button', { hasText: 'Save' }).click()
      await expect(page.locator('text=/key saved/i')).toBeVisible({
        timeout: 8000,
      })
    }

    // Reload settings and verify all four providers show configured + Rotate
    await page.goto('/settings')
    for (const provider of PROVIDERS) {
      const card = page.getByTestId(`provider-card-${provider}`)
      await expect(card.locator('p', { hasText: /^configured$/ })).toBeVisible({
        timeout: 5000,
      })
      await expect(card.locator('button', { hasText: 'Rotate' })).toBeVisible({
        timeout: 5000,
      })
    }
  })

  // AC6: using Rotate to enter a new value keeps the provider showing "configured"
  test('AC6: rotating a key keeps the provider showing configured', async ({
    page,
  }) => {
    await page.goto('/settings')
    const card = page.getByTestId('provider-card-fmp')

    // Ensure fmp has an initial key (idempotent — PUT overwrites existing)
    const cancelBtn = card.locator('button', { hasText: 'Cancel' })
    if (await cancelBtn.isVisible()) await cancelBtn.click()
    await card.locator('button', { hasText: 'Rotate' }).click()
    await page.locator('input[placeholder*="Paste new API key"]').fill('fmp-key-v1')
    await card.locator('button', { hasText: 'Save' }).click()
    await expect(page.locator('text=/key saved/i')).toBeVisible({ timeout: 8000 })

    // Rotate with a new value
    await card.locator('button', { hasText: 'Rotate' }).click()
    await page.locator('input[placeholder*="Paste new API key"]').fill('fmp-key-v2')
    await card.locator('button', { hasText: 'Save' }).click()
    await expect(page.locator('text=/key saved/i')).toBeVisible({ timeout: 8000 })

    // Provider still shows configured with Rotate button
    await expect(card.locator('p', { hasText: /^configured$/ })).toBeVisible({
      timeout: 5000,
    })
    await expect(card.locator('button', { hasText: 'Rotate' })).toBeVisible({
      timeout: 5000,
    })
  })
})
