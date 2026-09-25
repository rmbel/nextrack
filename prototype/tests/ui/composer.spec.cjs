const {test, expect} = require('@playwright/test');

test.beforeEach(async ({page}) => { await page.goto('/'); });

test('long prompt grows and moves like without clipping or horizontal overflow', async ({page}) => {
  const prompt = page.getByLabel('Playlist prompt', {exact: true});
  const like = page.locator('#like-token');
  await prompt.fill('chill');
  const before = await like.boundingBox();
  const small = await prompt.boundingBox();
  await prompt.fill('recent bachata songs for the last part of a summer party that slowly becomes calmer, with warm acoustic sounds and romantic voices');
  const after = await like.boundingBox();
  const large = await prompt.boundingBox();
  expect(large.width).toBeGreaterThan(small.width);
  expect(after.y > before.y + 5 || after.x > before.x + 20).toBeTruthy();
  expect(after.y >= large.y + large.height - 2 || after.x >= large.x + large.width - 2).toBeTruthy();
  const geometry = await prompt.evaluate(el => ({font: parseFloat(getComputedStyle(el).fontSize),
    height: el.clientHeight, contentHeight: el.scrollHeight, width: el.clientWidth, contentWidth: el.scrollWidth}));
  expect(geometry.font).toBeGreaterThanOrEqual(18);
  expect(geometry.contentHeight).toBeLessThanOrEqual(geometry.height + 1);
  expect(geometry.contentWidth).toBeLessThanOrEqual(geometry.width + 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('500-character prompt remains readable and restart shrinks the composer', async ({page}) => {
  const prompt = page.getByLabel('Playlist prompt', {exact:true});
  const initial = await prompt.boundingBox();
  await prompt.fill('warm acoustic songs with soft vocals and a relaxed atmosphere '.repeat(8).slice(0,500));
  const expanded = await prompt.boundingBox();
  expect(expanded.height).toBeGreaterThan(initial.height);
  await expect(page.getByRole('button',{name:'Recommend',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Restart',exact:true}).click();
  await expect(prompt).toHaveValue('');
  expect((await prompt.boundingBox()).height).toBeLessThan(expanded.height);
  await expect(page.getByRole('button',{name:'Recommend',exact:true})).toBeDisabled();
});

test('keyboard selection and recommendations show songs without algorithm metrics', async ({page}) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const search = page.getByLabel('Song search',{exact:true});
  await search.fill('La Bachata');
  await expect(page.locator('#ghost-suggestion')).toBeVisible();
  await expect(page.locator('#accept-suggestion, #next-suggestion')).toHaveCount(0);
  await search.press('Tab');
  await expect(page.locator('#seed-counter')).toHaveText('1 selected');
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#results .result-card')).toHaveCount(5);
  await expect(page.locator('#strategy-result, #result-metrics, #results .reason')).toHaveCount(0);
  await expect(page.locator('#results')).not.toContainText(/seed|prompt|strategy|similarity/i);
  expect(errors).toEqual([]);
  await page.getByRole('button',{name:'Remove La Bachata',exact:true}).click();
  await expect(page.locator('.selected-track-card')).toHaveCount(0);
});

test('Enter selection supports four seeds and restart clears results', async ({page}) => {
  const search = page.getByLabel('Song search',{exact:true});
  for (const song of ['La Bachata', 'Propuesta Indecente','Eres Mia','Incondicional']) {
    await search.fill(song);
    await expect(page.locator('#ghost-suggestion')).toBeVisible();
    await search.press('Enter');
  }
  await expect(page.locator('.selected-track-card')).toHaveCount(4);
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#results .result-card')).toHaveCount(5);
  await page.getByRole('button',{name:'Restart',exact:true}).click();
  await expect(page.locator('#results-panel')).toBeHidden();
  await expect(page.locator('.selected-track-card')).toHaveCount(0);
});

test('prompt-only API errors are readable and retry stays available', async ({page}) => {
  await page.getByLabel('Playlist prompt',{exact:true}).fill('music for dinner');
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#request-status')).toContainText('Retry or add a song');
  await expect(page.getByRole('button',{name:'Recommend',exact:true})).toBeEnabled();
});

test('MusicBrainz song can be searched, selected and used for recommendations', async ({page}) => {
  const search=page.getByLabel('Song search',{exact:true});
  await search.fill('Te Gusta El Reggaeton');
  await expect(page.locator('#ghost-suggestion')).toContainText('Manny Rod');
  await search.press('Enter');
  await expect(page.locator('.selected-track-card')).toContainText('Manny Rod');
  const response=page.waitForResponse(r => r.url().endsWith('/recommend') && r.request().method()==='POST');
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  const result=await (await response).json();
  expect(result.seed_tracks[0].source).toBe('musicbrainz');
  expect(result.seed_tracks[0].id).toBe('musicbrainz:b8dde376-4eb4-447b-988f-b5a4e1306a01');
  await expect(page.locator('#results .result-card')).toHaveCount(5);
});

test('listener-ranked songs can be searched and recommended with data credits', async ({page}) => {
  test.skip(process.env.NEXTTRACK_FULL_CATALOG_TESTS !== '1', 'Requires the separately rebuilt research catalogue');
  const search = page.getByLabel('Song search', {exact:true});
  await search.fill('Revelry Kings Of Leon');
  await expect(page.locator('#ghost-suggestion')).toContainText('Revelry');
  await search.press('Enter');
  await expect(page.locator('.selected-track-card')).toContainText('Kings Of Leon');
  const response = page.waitForResponse(r => r.url().endsWith('/recommend') && r.request().method() === 'POST');
  await page.getByRole('button', {name:'Recommend', exact:true}).click();
  const result = await (await response).json();
  expect(result.seed_tracks[0].id).toBe('taste-profile:SOSXLTC12AF72A7F54');
  await expect(page.locator('#results .result-card')).toHaveCount(5);
  const sourceCount = result.recommendations.filter(t => t.source === 'taste-profile').length;
  expect(sourceCount).toBeGreaterThan(0);
  await expect(page.locator('#results .source-link', {hasText:'Taste Profile'})).toHaveCount(sourceCount);
  await expect(page.getByRole('link', {name:'Data credits', exact:true})).toHaveAttribute('href', '/static/catalogue-sources.html');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('expanded catalogue searches FMA songs and renders their source links', async ({page, request}) => {
  test.skip(process.env.NEXTTRACK_FULL_CATALOG_TESTS !== '1', 'Requires the separately rebuilt research catalogue');
  const health = await (await request.get('/health')).json();
  expect(health.catalogue_size).toBe(69710);
  const search = page.getByLabel('Song search', {exact:true});
  await search.fill('Freeway Kurt Vile');
  await expect(page.locator('#ghost-suggestion')).toContainText('Freeway');
  await search.press('Enter');
  await expect(page.locator('.selected-track-card')).toContainText('Kurt Vile');
  const response = page.waitForResponse(r => r.url().endsWith('/recommend') && r.request().method() === 'POST');
  await page.getByRole('button', {name:'Recommend', exact:true}).click();
  const result = await (await response).json();
  expect(result.seed_tracks[0].id).toBe('fma:10');
  const fmaCount = result.recommendations.filter(t => t.source === 'fma').length;
  expect(fmaCount).toBeGreaterThan(0);
  await expect(page.locator('#results .source-link', {hasText:'Free Music Archive'})).toHaveCount(fmaCount);
  for (const link of await page.locator('#results .source-link', {hasText:'Free Music Archive'}).all()) {
    await expect(link).toHaveAttribute('href', /^https:\/\/freemusicarchive.org\//);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('playlist feedback uses one required star rating and resets for new results', async ({page}) => {
  let saved;
  await page.route('**/feedback', async route => {
    saved=route.request().postDataJSON();
    await route.fulfill({status:201,contentType:'application/json',body:'{"status":"recorded"}'});
  });
  const search=page.getByLabel('Song search',{exact:true});
  await search.fill('La Bachata');
  await expect(page.locator('#ghost-suggestion')).toBeVisible();
  await search.press('Enter');
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#results .result-card')).toHaveCount(5);
  await expect(page.getByRole('radio')).toHaveCount(5);
  await expect(page.locator('#would-add, #prompt-fit-rating, #relevance-rating')).toHaveCount(0);
  await page.getByRole('button',{name:'Save rating',exact:true}).click();
  expect(saved).toBeUndefined();
  await page.getByRole('radio',{name:'4 stars',exact:true}).check();
  await expect(page.getByRole('radio',{name:'4 stars',exact:true})).toBeChecked();
  await page.getByRole('button',{name:'Save rating',exact:true}).click();
  await expect(page.locator('#feedback-status')).toHaveText('Saved');
  expect(saved.rating).toBe(4);
  expect(Object.keys(saved).sort()).toEqual(['comment','rating','request_id']);
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#feedback-status')).toHaveText('');
  await expect(page.locator('input[name=rating]:checked')).toHaveCount(0);
});

test('Spotify login is above composer and songs stay in one line with listening links', async ({page}) => {
  const login=page.getByRole('link',{name:'Log in with Spotify',exact:true});
  await expect(login).toHaveAttribute('href','/auth/spotify/login');
  expect((await login.boundingBox()).y).toBeLessThan((await page.locator('.composer').boundingBox()).y);
  const search=page.getByLabel('Song search',{exact:true});
  await search.fill('La Bachata');
  await expect(page.locator('#ghost-suggestion')).toBeVisible();
  await search.press('Enter');
  await expect(page.locator('.selected-track-card .spotify-play')).toHaveCount(1);
  await page.getByRole('button',{name:'Recommend',exact:true}).click();
  await expect(page.locator('#results .spotify-play')).toHaveCount(5);
  for (const row of await page.locator('#results .result-card').all()) {
    const line=row.locator('.song-line');
    expect(await line.evaluate(el=>getComputedStyle(el).whiteSpace)).toBe('nowrap');
    await expect(row.locator('.result-meta')).toContainText(/·.*·.*·/);
    await expect(row.locator('.spotify-play')).toHaveAttribute('href',/^\/spotify\/open\//);
    await expect(row.locator('.spotify-play')).toHaveAttribute('target','_blank');
    expect((await row.boundingBox()).height).toBeLessThan(90);
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('songs without a release year remain searchable and show year unknown in compact rows', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const unknown = {id: 'ui-fixture:unknown-year', title: 'Undated Song', artist: 'Fixture Artist', year: null, genre: 'Rock'};
  const dated = {id: 'ui-fixture:known-year', title: 'Dated Song', artist: 'Another Fixture Artist', year: 1998, genre: 'Pop'};
  await page.route('**/search?*', route => route.fulfill({json: [unknown]}));
  await page.route('**/recommend', route => route.fulfill({json: {
    request_id: 'ui-fixture-year-display', warnings: [], recommendations: [unknown, dated]
  }}));
  const search = page.getByLabel('Song search', {exact: true});
  await search.fill('Undated');
  await expect(page.locator('#ghost-suggestion')).toContainText('Undated Song - Fixture Artist');
  await search.press('Enter');
  await expect(page.locator('.selected-copy')).toHaveText('Undated Song · Fixture Artist · year unknown · Rock');
  await page.getByRole('button', {name: 'Recommend', exact: true}).click();
  await expect(page.locator('#results .result-card')).toHaveCount(2);
  await expect(page.locator('#results .song-line').nth(0)).toHaveText('Undated Song · Fixture Artist · year unknown · Rock');
  await expect(page.locator('#results .song-line').nth(1)).toHaveText('Dated Song · Another Fixture Artist · 1998 · Pop');
  for (const line of await page.locator('.selected-copy, #results .song-line').all()) {
    expect(await line.evaluate(element => getComputedStyle(element).whiteSpace)).toBe('nowrap');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test('live catalogue songs keep compact rows and Spotify links without retrieval labels', async ({page}) => {
  const tracks = Array.from({length: 5}, (_, i) => ({
    id: `musicbrainz:aaaaaaaa-aaaa-aaaa-aaaa-${String(i + 1).padStart(12, '0')}`,
    title: `Discovered Song ${i + 1}`, artist: `Artist ${i + 1}`, year: 2025, genre: 'jazz',
    source: 'musicbrainz'
  }));
  await page.route('**/recommend', route => route.fulfill({json: {
    request_id: 'ui-online-discovery-fixture', warnings: [], recommendations: tracks,
    verification: {discovery: {status: 'cache_hit', network_calls: 0}}
  }}));
  await page.getByLabel('Playlist prompt', {exact: true}).fill('instrumental jazz from 2025');
  await page.getByRole('button', {name: 'Recommend', exact: true}).click();
  await expect(page.locator('#results .result-card')).toHaveCount(5);
  await expect(page.locator('#results .song-line').first()).toHaveText('Discovered Song 1 · Artist 1 · 2025 · jazz');
  await expect(page.locator('#results .spotify-play').first()).toHaveAttribute('href', /musicbrainz.*aaaaaaaa/);
  await expect(page.locator('#results')).not.toContainText(/cache_hit|network_calls|prompt interpretation/i);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
