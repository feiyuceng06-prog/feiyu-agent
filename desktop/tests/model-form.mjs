import assert from 'node:assert/strict';

// Exercise the real renderer; intercept send before entering any fake credential.
export async function checkModelForm(page) {
  const passed = await page.evaluate(() => {
    const original = { send, models, editing };
    const sent = [];
    const passed = [];
    const check = (ok, name) => { if (!ok) throw new Error(name); passed.push(name); };
    const response = ticket => gotModelList({ ticket, ok: true, rows: [{ id: 'old-provider-model' }] });
    const mount = () => {
      editing = '';
      const form = modelForm();
      $('shelf-pane').replaceChildren(form);
      return form;
    };
    const button = (form, text) => [...form.querySelectorAll('button')].find(b => b.textContent === text);
    const type = (field, value) => { field.value = value; field.dispatchEvent(new Event('input', { bubbles: true })); };
    try {
      send = msg => { sent.push(msg); return true; };
      models = { trusted: true, rows: [] };
      let form = mount();
      let got = form.modelForm.got;
      type(got.base_url, 'https://api.openai.com/v1');
      type(got.key, 'test-only-openai-key');
      got.key.dispatchEvent(new Event('change', { bubbles: true }));
      check(sent.length === 0, 'entering/blurring key never sends a request');
      form.querySelector('[data-name="DeepSeek"]').click();
      check(got.key.value === '' && sent.length === 0, 'switching preset clears key without transmitting it');
      type(got.key, 'test-only-deepseek-key');
      button(form, '列出模型').click();
      const current = sent.at(-1);
      check(current.base_url === 'https://api.deepseek.com' && current.key === 'test-only-deepseek-key', 'explicit request uses current endpoint/key');
      response(current.ticket);
      check(!form.modelForm.picks.hidden, 'current response displays models');
      type(got.base_url, 'https://other.example/v1');
      response(current.ticket);
      check(got.key.value === '' && form.modelForm.picks.hidden && !form.modelForm.picks.textContent, 'URL change clears key and rejects old response');

      for (const change of ['kind', 'key', 'new-form', 'cancel', 'save', 'programmatic-url']) {
        form = mount(); got = form.modelForm.got;
        type(got.base_url, 'https://example.test/v1');
        type(got.key, 'fake-key');
        got.model.value = 'model';
        button(form, '列出模型').click();
        const ticket = sent.at(-1).ticket;
        if (change === 'kind') {
          const kind = form.querySelector('select'); kind.value = 'anthropic'; kind.dispatchEvent(new Event('change'));
          check(got.key.value === '', 'provider type change clears key');
        } else if (change === 'key') type(got.key, 'fake-replacement');
        else if (change === 'new-form') form = mount();
        else if (change === 'cancel') { button(form, '取消').click(); form = mount(); }
        else if (change === 'save') button(form, '保存').click();
        else got.base_url.value = 'https://changed.example/v1';
        response(ticket);
        check(form.modelForm.picks.hidden && !form.modelForm.picks.textContent, `${change} rejects stale response`);
      }
      return passed;
    } finally {
      send = original.send; models = original.models; editing = original.editing;
      drawShelf();
    }
  });
  assert.equal(passed.length, 12);
  console.log(`PASS: ${passed.length} model form credential/race checks`);
}
