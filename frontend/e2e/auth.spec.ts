import { test, expect } from '@playwright/test'
import { PASSWORD, signIn } from './helpers'

test.describe('Authentication', () => {
  test('sign-in flow: empty password shows error, does not set session cookie', async ({
    page,
    context,
  }) => {
    await page.goto('/')
    await page.waitForSelector('input[type="password"]', { timeout: 8000 })

    // Submit without filling the password field
    await page.click('button[type="submit"]')

    await expect(page.locator('text=/password is required/i')).toBeVisible({
      timeout: 5000,
    })
    // Form still visible — not redirected
    await expect(page.locator('input[type="password"]')).toBeVisible()
    // No session cookie set
    const cookies = await context.cookies()
    expect(cookies.find((c) => c.name === 'sa_session')).toBeUndefined()
  })

  test('sign-in flow: wrong password shows error, does not set session cookie', async ({
    page,
    context,
  }) => {
    await page.goto('/')
    await page.waitForSelector('input[type="password"]', { timeout: 8000 })

    await page.fill('input[type="password"]', 'wrong-password')
    await page.click('button[type="submit"]')

    await expect(page.locator('text=/invalid password/i')).toBeVisible({
      timeout: 5000,
    })
    // Form still visible — not redirected
    await expect(page.locator('input[type="password"]')).toBeVisible()
    // No session cookie set
    const cookies = await context.cookies()
    expect(cookies.find((c) => c.name === 'sa_session')).toBeUndefined()
  })

  test('sign-in flow: correct password sets HTTP-only session cookie and lands on Dashboard View', async ({
    page,
    context,
  }) => {
    await page.goto('/')
    await page.waitForSelector('input[type="password"]', { timeout: 8000 })

    await page.fill('input[type="password"]', PASSWORD)
    await page.click('button[type="submit"]')

    // After login the new-job form should appear (Dashboard View)
    await expect(page.locator('textarea#subject')).toBeVisible({ timeout: 8000 })

    // HTTP-only session cookie is set
    const cookies = await context.cookies()
    const sessionCookie = cookies.find((c) => c.name === 'sa_session')
    expect(sessionCookie).toBeDefined()
    expect(sessionCookie?.httpOnly).toBe(true)
  })

  test('sign-in flow: authenticated state persists across page navigations', async ({
    page,
  }) => {
    await signIn(page)

    // Navigate to /history and back to / — should not show sign-in
    await page.goto('/history')
    await expect(page.locator('h1', { hasText: /history/i })).toBeVisible({
      timeout: 5000,
    })
  })
})
