import { test, expect } from '@playwright/test';

for (const surface of ['desktop', 'mobile']) {
  for (const tool of ['pi', 'hermes']) {
    test(`${surface} selects ${tool} CommandCode context and exact model`, async ({ page }) => {
      const model = 'deepseek/deepseek-v4.1-flash';
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let submitted;
      await page.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/sessions' && route.request().method() === 'POST') {
          submitted = route.request().postDataJSON();
          return route.fulfill({ status: 400, json: { detail: 'test captured request' } });
        }
        if (path === '/api/delegations') return route.fulfill({json:{roots:[],max_children_per_parent:2}});
        if (path === '/api/me') return route.fulfill({ json: {
          login: 'test', access_surface: 'local-lan', default_tool: 'shell',
          profiles: [{ name: 'general', display_name: 'General', read_write_capability: 'write', worktree_requirement: 'none' }],
          tool_status: ['shell', 'pi', 'hermes'].map(name => ({name, status:'ready'})),
          auth_contexts: ['pi','hermes'].map(name => ({tool:name,name:'commandcode-main',provider:'commandcode',default:true,enabled:true,status:'ready',model,models:[model]})).concat([{tool:'shell',name:'default',default:true,status:'ready'}]),
        }});
        return route.fulfill({ json: [] });
      });
      await page.goto(surface === 'mobile' ? '/mobile' : '/desktop#new');
      if (surface === 'mobile') await page.locator('[data-mobile-tab="new"]').click();
      const form = page.locator(surface === 'mobile' ? '#mobile-new' : '#new-session-form');
      // Desktop form ID is intentionally discovered from the tool control.
      const actualForm = surface === 'mobile' ? form : page.locator('form').filter({has:page.locator('select[name="tool"]')}).first();
      await actualForm.locator('[name="tool"]').selectOption(tool);
      await expect(actualForm.locator('[name="auth_context"]')).toHaveValue('commandcode-main');
      await expect(actualForm.locator('[name="model"]')).toBeVisible();
      await expect(actualForm.locator('[name="model"]')).toHaveValue(model);
      await expect(actualForm.locator('[name="agent_mode"]')).toBeDisabled();
      await actualForm.locator('button[type="submit"]').click();
      await expect.poll(() => submitted?.tool).toBe(tool);
      expect(submitted.model).toBe(model);
      expect(submitted.provider).toBeNull();
      expect(submitted.agent_mode).toBeNull();
      expect(errors).toEqual([]);
    });
  }
}
