// PPT Studio 프론트엔드 (프레임워크 없음)
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const state = {
  pid: null, proj: null, sel: null, tab: "work", checked: new Set(), draft: {}, verSel: {}, lastJson: "",
  settings: {}, env: {}, layouts: {}, pen: { no: null, on: false, strokes: [], marks: [] }, view: {}, showIds: false,
  chatBusy: false,
};
const STEP_ORDER = [
  ["기획", ["planning"]], ["기획 컨펌", ["plan_review"]], ["장표 제작", ["building"]],
  ["확인 · 수정", ["review", "revising"]], ["완료", ["done"]],
];

async function api(path, opt = {}) {
  const r = await fetch(path, opt);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.blob();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const postForm = (path, fd) => api(path, { method: "POST", body: fd });
const fileUrl = rel => `/api/projects/${state.pid}/file/${rel.split("/").map(encodeURIComponent).join("/")}`;
const toast = msg => alert(msg);
const fmtN = n => Number(n || 0).toLocaleString();

// ---------------------------------------------------------------- 라우팅
window.addEventListener("hashchange", route);
async function route() {
  const m = location.hash.match(/^#\/p\/([\w-]+)/);
  state.pid = m ? m[1] : null;
  state.lastJson = "";
  if (state.pid) await refresh(); else home();
}

// ---------------------------------------------------------------- 홈
async function home() {
  $("#crumb").textContent = "";
  const list = await api("/api/projects");
  $("#app").innerHTML = `${envBanner()}
  <div class="grid" style="grid-template-columns:1fr 420px;align-items:start">
    <section>
      <h2>프로젝트</h2>
      <div class="plist">${list.map(p => `
        <a href="#/p/${p.id}" class="card"><b>${esc(p.name)}</b>
          <div class="muted small" style="margin-top:6px">장표 ${p.pages}장 · 완료 ${p.done}장</div></a>`).join("") || `<p class="muted">아직 프로젝트가 없습니다.</p>`}
      </div>
    </section>
    <form class="card" id="newp">
      <h3>새 프로젝트</h3>
      <label>이름 <input name="name" placeholder="예: 페어트리 IR Deck 리디자인" required></label>
      <label>요청사항 / 톤앤매너
        <textarea name="request" rows="5" placeholder="예: 귀엽고 따뜻한 분위기가 살아 있으면서도 IR답게 한눈에 들어오는 디자인. 귀엽지만 유치하지 않게. AI스럽지 않게."></textarea></label>
      <label>원본 덱 — <b>PPTX 권장</b> (테마·마스터를 그대로 유지하고, 완성 장표만 교체해서 돌려드려요)
        <div class="drop"><input type="file" name="source" multiple accept=".pdf,.pptx,.png,.jpg,.jpeg,.webp"></div></label>
      <label>레퍼런스 이미지 (톤앤매너)<div class="drop"><input type="file" name="refs" multiple accept="image/*"></div></label>
      <label>실제 에셋 (로고, 캐릭터 PNG, 게임 스크린샷, 굿즈 사진)<div class="drop"><input type="file" name="assets" multiple accept="image/*"></div></label>
      <button class="primary" style="width:100%;margin-top:6px">만들고 내용 파악 시작</button>
    </form>
  </div>`;
  $("#newp").onsubmit = async e => {
    e.preventDefault();
    const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "업로드 중...";
    try { const r = await postForm("/api/projects", new FormData(e.target)); location.hash = `#/p/${r.id}`; }
    catch (err) { toast(err.message); btn.disabled = false; btn.textContent = "만들고 내용 파악 시작"; }
  };
}

function envBanner() {
  const e = state.env, out = [];
  if (e.python_pptx === false) out.push(`<b>python-pptx 미설치</b> — PPTX를 만들 수 없습니다. run 스크립트로 실행해 주세요.`);
  if (e.pretendard === false) out.push(`<b>Pretendard 폰트가 이 PC에 없습니다</b> — PowerPoint에서 다른 폰트로 보일 수 있어요. <a href="https://github.com/orioncactus/pretendard/releases" target="_blank">설치하기</a>`);
  if (e.preview === "") out.push(`PowerPoint/LibreOffice가 없어 <b>브라우저 미리보기(근사치)</b>로 보여드립니다. 정확한 미리보기는 PowerPoint가 있는 Windows에서 가능해요.`);
  return out.length ? `<div class="warns" style="margin-bottom:14px">${out.map(x => `<div>${x}</div>`).join("")}</div>` : "";
}

// ---------------------------------------------------------------- 프로젝트
async function refresh() {
  if (!state.pid) return;
  let p;
  try { p = await api(`/api/projects/${state.pid}`); } catch (e) { $("#app").innerHTML = `<p>${esc(e.message)}</p>`; return; }
  const js = JSON.stringify(p);
  if (js === state.lastJson) return;
  state.lastJson = js; state.proj = p;
  $("#crumb").textContent = "/ " + p.name;
  const slides = sortedSlides();
  if (state.sel == null || !p.slides[state.sel]) state.sel = slides.length ? String(slides[0].no) : null;
  render();
}
const sortedSlides = () => Object.values(state.proj.slides).sort((a, b) => a.no - b.no);
const relayOf = no => (state.proj.relay || []).filter(r => r.no === no);
const isAI = s => !!s.job && !relayOf(s.no).length;
const isUser = s => (!s.job && state.proj.user_turn.includes(s.stage)) || relayOf(s.no).length > 0;
const curIdx = s => !s.versions.length ? -1 : (s.approved >= 0 && s.approved < s.versions.length ? s.approved : s.versions.length - 1);

function nowBanner() {
  const p = state.proj, sl = sortedSlides();
  if (p.relay?.length) return { cls: "user", title: `복붙 차례 — ${p.relay.length}건`, sub: "아래 카드의 프롬프트를 ChatGPT에 붙여넣고, 답을 다시 붙여넣어 주세요." };
  if (p.job) return { cls: "ai", title: `AI 작업 중 — ${p.job.label}`, sub: "끝나면 자동으로 갱신됩니다." };
  if (p.error) return { cls: "user", title: "오류가 있었습니다", sub: p.error };
  if (!p.pages.length) return { cls: "user", title: "원본 덱을 올려주세요", sub: "[자료] 탭에서 PPTX/PDF/이미지를 올리면 내용 파악부터 시작합니다." };
  const mine = sl.filter(isUser), ai = sl.filter(isAI);
  if (mine.length) {
    const t = mine.map(s => `${s.no}번 ${p.stages[s.stage]}`).join(" · ");
    return { cls: "user", title: `내 차례 — ${t}`, sub: ai.length ? `동시에 AI 작업 중: ${ai.map(s => `${s.no}번 ${s.job.label}`).join(", ")}` : "장표를 눌러 확인하고 ㅇㅋ 또는 피드백을 주세요.", go: mine[0].no };
  }
  if (ai.length) return { cls: "ai", title: `AI 작업 중 — ${ai.map(s => `${s.no}번 ${s.job.step || s.job.label}`).join(" · ")}`, sub: "끝나면 자동으로 갱신됩니다." };
  const todo = sl.filter(s => s.stage === "todo");
  if (todo.length) {
    const n = state.settings.batch_size || 3, next = todo.slice(0, n).map(s => s.no);
    return { cls: "", title: `다음 작업 — ${next.join(", ")}번 장표 기획`, sub: "버튼 한 번이면 선택한 장표들의 기획안을 동시에 받아옵니다.", batch: next };
  }
  return { cls: "", title: "모든 장표 완료 🎉", sub: "전체 덱 PPTX를 내려받으세요.", deck: true };
}

function render() {
  const p = state.proj, nb = nowBanner(), u = p.usage || {};
  const anyDone = sortedSlides().some(s => s.stage === "done");
  $("#app").innerHTML = `${envBanner()}
    <div class="now ${nb.cls}"><span class="dot"></span>
      <div style="flex:1"><div class="title">${esc(nb.title)}</div><div class="muted small">${esc(nb.sub)}</div></div>
      ${nb.go ? `<button class="accent" onclick="selectSlide('${nb.go}')">${nb.go}번 열기</button>` : ""}
      ${nb.batch ? `<button class="accent" onclick="batch([${nb.batch}],'plan')">${nb.batch.join(", ")}번 기획 시작</button>` : ""}
      ${anyDone ? `<a href="/api/projects/${p.id}/deck.pptx"><button class="primary" title="${p.template ? "원본 PPTX에서 완료 장표만 교체" : "완료 장표만 모아서"}">전체 덱 PPTX</button></a>` : ""}
    </div>
    ${relayPanel()}
    <div class="tabs">${[["work", "장표 작업"], ["summary", "내용 파악"], ["guide", "디자인 가이드"], ["files", "자료"]].map(([k, v]) =>
      `<button class="${state.tab === k ? "on" : ""}" onclick="setTab('${k}')">${v}</button>`).join("")}
      <span class="spacer"></span><span class="muted small" style="align-self:center" title="이 프로젝트에서 쓴 API">API 사용: 텍스트 ${fmtN(u.text_calls)}회 (${fmtN((u.input_tokens || 0) + (u.output_tokens || 0))} 토큰) · 이미지 ${fmtN(u.images)}장</span></div>
    <div id="tab"></div>`;
  ({ work: renderWork, summary: renderSummary, guide: renderGuide, files: renderFiles })[state.tab]();
  restoreDrafts();
  afterRender();
}
function setTab(t) { state.tab = t; render(); }

// ---------------- 복붙 대기함
function relayPanel() {
  const items = state.proj.relay || [];
  if (!items.length) return "";
  return `<div class="relay">${items.map((r, n) => `
    <div class="rcard">
      <div class="row" style="justify-content:space-between">
        <div><span class="badge user">복붙 ${n + 1}/${items.length}</span> <b>${r.no != null ? `${r.no}번 장표 · ` : ""}${esc(r.label)}</b>
          ${r.step ? `<span class="muted small"> — ${esc(r.step)}</span>` : ""}</div>
        <div class="row"><a href="https://chatgpt.com/" target="chatgpt"><button class="ghost small">ChatGPT 열기 ↗</button></a>
          <button class="ghost small" onclick="relayAct('${r.id}','cancel')">취소</button></div>
      </div>
      ${r.note ? `<div class="warns">${esc(r.note)}</div>` : ""}
      <ol class="rsteps">
        <li><button class="accent" onclick="copyPrompt('${r.id}', this)">① 프롬프트 복사</button>
          <span class="muted small">ChatGPT <b>새 대화</b>에 붙여넣기 ${r.kind === "image" ? "(이미지 생성 요청)" : ""}</span>
          <details class="small"><summary>프롬프트 보기 (${r.text.length.toLocaleString()}자)</summary><pre class="rtext">${esc(r.text)}</pre></details></li>
        ${r.images.length ? `<li><span>② 이미지 ${r.images.length}장 첨부</span> <span class="muted small">— 끌어다 놓거나 [복사] 후 ChatGPT에 Ctrl+V (순서대로)</span>
          <div class="thumbs" style="margin-top:6px">${r.images.map((f, i) => `<div class="thumb"><img draggable="true" src="/api/relay/${r.id}/image/${i}" title="${esc(f)}">
            <span class="num">${i + 1}</span><button onclick="copyImage('${r.id}',${i},this)">복사</button></div>`).join("")}</div></li>` : ""}
        <li>${r.kind === "image"
          ? `<span>${r.images.length ? "③" : "②"} 생성된 이미지 저장 후 올리기</span>
             <div class="row" style="margin-top:6px"><input type="file" id="rfile-${r.id}" accept="image/*" style="width:auto">
             <button class="primary" onclick="relayImage('${r.id}')">올리기</button>
             <button class="ghost" onclick="relayAct('${r.id}','skip')">이 에셋 건너뛰기 (자리표시로)</button></div>
             <div class="muted small">또는 이미지를 복사한 뒤 아래 칸에 Ctrl+V</div>
             <div class="pastebox" tabindex="0" onpaste="relayPasteImage(event,'${r.id}')">여기를 클릭하고 Ctrl+V</div>`
          : `<span>${r.images.length ? "③" : "②"} ChatGPT 답변 전체를 복사해서 붙여넣기</span> ${r.json ? `<span class="muted small">(코드블록 오른쪽 위 [복사] 버튼)</span>` : ""}
             <textarea id="relay-${r.id}" rows="5" placeholder="답변 붙여넣기" style="margin-top:6px"></textarea>
             <div class="row" style="margin-top:6px"><button class="primary" onclick="relayAnswer('${r.id}')">답변 넣고 계속 진행</button>
             <button class="ghost small" onclick="pasteFromClipboard('${r.id}')">클립보드에서 바로 붙여넣기</button></div>`}</li>
      </ol>
    </div>`).join("")}</div>`;
}
const relayById = id => (state.proj.relay || []).find(r => r.id === id);
async function copyPrompt(id, btn) {
  const r = relayById(id); if (!r) return;
  try { await navigator.clipboard.writeText(r.text); }
  catch { const t = document.createElement("textarea"); t.value = r.text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
  btn.textContent = "✓ 복사됨"; setTimeout(() => btn.textContent = "① 프롬프트 복사", 1500);
}
async function copyImage(id, i, btn) {
  try {
    const blob = await (await fetch(`/api/relay/${id}/image/${i}`)).blob();
    let png = blob;
    if (blob.type !== "image/png") {
      const bmp = await createImageBitmap(blob), c = document.createElement("canvas");
      c.width = bmp.width; c.height = bmp.height; c.getContext("2d").drawImage(bmp, 0, 0);
      png = await new Promise(r => c.toBlob(r, "image/png"));
    }
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
    btn.textContent = "✓";
  } catch (e) { toast("이미지 복사가 안 되는 브라우저예요. 이미지를 끌어다 놓아 주세요. (" + e.message + ")"); }
}
async function pasteFromClipboard(id) {
  try { const t = await navigator.clipboard.readText(); const el = $(`#relay-${id}`); el.value = t; state.draft[`relay-${id}`] = t; }
  catch { toast("브라우저가 클립보드 읽기를 막았어요. 칸을 클릭하고 Ctrl+V 해주세요."); }
}
async function relayAnswer(id) {
  const t = $(`#relay-${id}`).value; if (!t.trim()) return toast("답변을 붙여넣어 주세요.");
  const fd = new FormData(); fd.append("action", "answer"); fd.append("answer", t);
  try { await postForm(`/api/relay/${id}`, fd); delete state.draft[`relay-${id}`]; state.lastJson = ""; setTimeout(refresh, 300); } catch (e) { toast(e.message); }
}
async function relaySend(id, blob, name) {
  const fd = new FormData(); fd.append("action", "answer"); fd.append("file", blob, name || "image.png");
  try { await postForm(`/api/relay/${id}`, fd); state.lastJson = ""; setTimeout(refresh, 300); } catch (e) { toast(e.message); }
}
function relayImage(id) { const f = $(`#rfile-${id}`).files[0]; if (!f) return toast("이미지를 선택해 주세요."); relaySend(id, f, f.name); }
function relayPasteImage(e, id) {
  const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith("image/"));
  if (!item) return toast("클립보드에 이미지가 없어요.");
  e.preventDefault(); relaySend(id, item.getAsFile(), "pasted.png");
}
async function relayAct(id, action) {
  if (action === "cancel" && !confirm("이 요청을 취소할까요? 해당 작업은 오류 상태가 되고 [다시 시도]로 이어갈 수 있어요.")) return;
  const fd = new FormData(); fd.append("action", action);
  try { await postForm(`/api/relay/${id}`, fd); state.lastJson = ""; setTimeout(refresh, 300); } catch (e) { toast(e.message); }
}

// ---------------- 장표 작업 탭
function renderWork() {
  const p = state.proj, sl = sortedSlides();
  if (!sl.length) { $("#tab").innerHTML = `<p class="muted">${p.job ? "원본을 분석하는 중입니다..." : "원본 덱이 없습니다. [자료] 탭에서 올려주세요."}</p>`; return; }
  const cur = p.slides[state.sel];
  $("#tab").innerHTML = `
  <div class="work">
    <aside>
      <div class="row" style="margin-bottom:8px">
        <button onclick="batchChecked('plan')" ${state.checked.size ? "" : "disabled"}>선택 ${state.checked.size}장 기획</button>
        <button class="ghost small" onclick="state.checked.clear();render()">선택 해제</button>
      </div>
      <div class="list">${sl.map(s => {
        const pg = p.pages.find(x => x.no === s.no) || {};
        const i = curIdx(s), thumb = i >= 0 && s.versions[i].preview ? s.versions[i].preview : pg.image;
        const cls = s.job ? "ai" : s.stage === "done" ? "done" : s.stage === "error" ? "err" : isUser(s) ? "user" : "";
        return `<div class="item ${String(s.no) === state.sel ? "on" : ""}" onclick="selectSlide('${s.no}')">
          <input type="checkbox" ${state.checked.has(s.no) ? "checked" : ""} onclick="event.stopPropagation();toggleCheck(${s.no})">
          ${thumb ? `<img src="${fileUrl(thumb)}">` : `<div class="noimg"></div>`}
          <div><div class="no">${s.no}번 ${s.kind === "cover" ? "· 표지" : ""}</div>
          <span class="badge ${cls}">${esc(s.job ? (s.job.step || s.job.label) : p.stages[s.stage])}</span></div></div>`;
      }).join("")}</div>
    </aside>
    <section>${cur ? slidePanel(cur) : ""}</section>
  </div>`;
}

function stepsBar(s) {
  const st = s.stage === "error" ? s.stage_before_error : s.stage;
  const idx = STEP_ORDER.findIndex(([, x]) => x.includes(st));
  return `<div class="steps">${STEP_ORDER.map(([name], i) =>
    `<div class="step ${i < idx ? "past" : i === idx ? "cur" : ""}">${i + 1}. ${name}</div>`).join("")}</div>`;
}

function verSel(s) { const v = state.verSel[s.no]; return v != null && v < s.versions.length ? v : curIdx(s); }

function slidePanel(s) {
  const p = state.proj, pg = p.pages.find(x => x.no === s.no) || {};
  const vi = verSel(s), v = s.versions[vi];
  const hasReal = v && v.preview, mode = state.view[s.no] || (hasReal ? "real" : "html");
  const srcLabel = x => x.source === "manual" ? "직접 수정" : x.note === "AI 자체 점검 수정" ? "자체 점검" : x.feedback ? "피드백" : "AI";
  return `
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0">${s.no}번 장표 ${s.kind === "cover" ? "(표지)" : ""}</h2>
      <div class="row small">
        <label class="check small" style="margin:0"><input type="checkbox" ${s.kind === "cover" ? "checked" : ""} onchange="opt(${s.no},{kind:this.checked?'cover':'normal'})"> 표지</label>
        <label class="check small" style="margin:0"><input type="checkbox" ${s.keep_game_images ? "checked" : ""} onchange="opt(${s.no},{keep_game_images:this.checked})"> 게임/제품 이미지 그대로 두기</label>
      </div>
    </div>
    ${stepsBar(s)}
    <div class="compare">
      <figure><figcaption>원본</figcaption>${pg.image ? `<a href="${fileUrl(pg.image)}" target="_blank"><img class="shot" src="${fileUrl(pg.image)}"></a>` : `<div class="shot empty">이미지 없음</div>`}</figure>
      <figure><figcaption>
          <b>결과 PPTX</b>
          ${s.versions.length ? `<span class="vers">${s.versions.map((x, i) => `<button class="${i === vi ? "on" : ""}" title="${esc(x.note)}${x.feedback ? " — " + esc(x.feedback) : ""}" onclick="pickVer(${s.no},${i})">v${i + 1}<small> ${srcLabel(x)}</small></button>`).join("")}</span>` : ""}
          ${s.approved >= 0 ? `<span class="badge done">확정 v${s.approved + 1}</span>` : ""}
          <span class="spacer"></span>
          ${v ? `<span class="seg">${hasReal ? `<button class="${mode === "real" ? "on" : ""}" onclick="setView(${s.no},'real')" title="PowerPoint가 실제로 렌더링한 이미지">실제 렌더</button>` : ""}<button class="${mode === "html" ? "on" : ""}" onclick="setView(${s.no},'html')" title="브라우저 근사 미리보기">브라우저</button></span>
          <label class="check small" style="margin:0"><input type="checkbox" ${state.showIds ? "checked" : ""} onchange="state.showIds=this.checked;render()"> 요소 ID</label>` : ""}
        </figcaption>
        ${v ? `<div class="stage-wrap" id="stage-${s.no}">
            ${mode === "real" && hasReal ? `<img class="shot" id="shot-${s.no}" src="${fileUrl(v.preview)}">` : `<div class="shot svgshot" id="svg-${s.no}" data-layout="${esc(v.layout)}"></div>`}
            <canvas class="pen ${state.pen.on && state.pen.no === s.no ? "on" : ""}" id="pen-${s.no}"></canvas>
          </div>
          <div class="row small" style="margin-top:6px">
            <button class="${state.pen.on && state.pen.no === s.no ? "accent" : ""}" onclick="togglePen(${s.no})">🖍 빨간 펜 ${state.pen.on && state.pen.no === s.no ? "켜짐" : ""}</button>
            ${state.pen.no === s.no && state.pen.on ? `<span id="pencount-${s.no}">표시 ${state.pen.marks.length}곳</span><button class="ghost" onclick="clearPen()">지우기</button><span class="muted">표시한 뒤 아래에 설명을 적고 [수정 요청]</span>` : ""}
            <span class="spacer"></span>
            ${v.pptx ? `<a href="/api/projects/${p.id}/slides/${s.no}/pptx"><button>이 버전 PPTX</button></a>` : ""}
          </div>`
          : `<div class="shot empty">${s.job ? esc(s.job.step || s.job.label) + "..." : "아직 없음"}</div>`}
      </figure>
    </div>
    ${s.warnings?.length ? `<div class="warns"><b>자동 검사</b> — 확인해 주세요<ul>${s.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    ${actionBox(s)}
    <div class="two">
      <div>
        <h3 style="margin-top:18px">기획안 ${s.plan_versions.length ? `<span class="muted small">(v${s.plan_versions.length + 1})</span>` : ""}</h3>
        ${s.plan ? `<textarea class="plan" id="plan-${s.no}" rows="16">${esc(s.plan)}</textarea>
          <div class="row" style="margin-top:6px"><button class="small" onclick="savePlan(${s.no})">직접 수정한 내용 저장</button>
          ${s.versions.length ? `<button class="small ghost" onclick="if(confirm('기획안 기준으로 장표를 처음부터 다시 만들까요?'))act(${s.no},'rebuild')">이 기획안으로 처음부터 다시 제작</button>` : ""}</div>`
          : `<p class="muted">${s.stage === "planning" ? "기획안 작성 중..." : "아직 기획안이 없습니다."}</p>`}
      </div>
      <div>${chatPanel(s)}</div>
    </div>
    ${s.log.length ? `<details style="margin-top:14px"><summary class="small muted">작업 기록 (${s.log.length})</summary><div class="log">${s.log.slice().reverse().map(l =>
      `<div>${new Date(l.ts * 1000).toLocaleTimeString()} · ${l.role === "user" ? "나" : "AI"} · ${esc(l.text)}</div>`).join("")}</div></details>` : ""}
  </div>`;
}

function chatPanel(s) {
  return `<h3 style="margin-top:18px">이 장표에 대해 물어보기</h3>
    <div class="chat" id="chat-${s.no}">${s.chat.map((m, i) => `<div class="msg ${m.role}">${esc(m.text)}
      ${m.role === "ai" && s.versions.length && !s.job ? `<div><button class="small ghost" onclick="chatToRevise(${s.no},${i})">↳ 이대로 수정 요청</button></div>` : ""}</div>`).join("")
      || `<div class="muted small">예: "디자인이 왜 AI스러워 보일까?", "e3 박스 없애면 어때?", "레퍼런스 3번 느낌이 나려면?"</div>`}
      ${state.chatBusy === s.no ? `<div class="msg ai muted">답변 작성 중...</div>` : ""}</div>
    <div class="row" style="margin-top:6px"><textarea id="chatin-${s.no}" rows="2" placeholder="메시지 (Ctrl+Enter 전송)" onkeydown="if(event.key==='Enter'&&(event.ctrlKey||event.metaKey))sendChat(${s.no})"></textarea>
    <button onclick="sendChat(${s.no})" ${state.chatBusy ? "disabled" : ""}>보내기</button></div>`;
}

function feedbackFields(no) {
  return `<textarea id="fb-${no}" rows="3" placeholder="예: 손글씨 빼줘 / 빨간 표시한 곳은 원형 그래프로 / 색 너무 많이 쓰지 마 / 메인 문구 한 줄로"></textarea>
    <div class="row" style="margin-top:6px">
      <select id="rule-${no}" style="width:auto"><option value="project">이 피드백을 규칙으로 저장 (이 프로젝트)</option><option value="global">규칙으로 저장 (모든 프로젝트)</option><option value="">규칙 저장 안 함 (이번만)</option></select>
    </div>`;
}

function actionBox(s) {
  const no = s.no, vi = verSel(s);
  if (s.job && relayOf(no).length) return `<div class="actbox"><h4>복붙 차례 — 위쪽 [복붙] 카드에서 ChatGPT 답을 넣어주세요</h4><div class="muted small">${esc(s.job.label)} · ${esc(s.job.step || "")}</div></div>`;
  if (s.job) return `<div class="actbox ai"><h4>AI 작업 중 — ${esc(s.job.label)}</h4><div class="muted small">${esc(s.job.step || "")} · 시작 ${new Date(s.job.started * 1000).toLocaleTimeString()} · 끝나면 자동 갱신</div></div>`;
  switch (s.stage) {
    case "todo": return `<div class="actbox"><h4>1. 기획부터 시작합니다</h4>
      <div class="muted small" style="margin-bottom:8px">${s.kind === "cover" ? "표지 기획 프롬프트" : "15년차 IR 디자이너 기획 프롬프트"}로 문구·레이아웃·시각화·에셋 계획을 받아옵니다.</div>
      <button class="accent" onclick="act(${no},'plan')">기획 시작</button></div>`;
    case "plan_review": return `<div class="actbox"><h4>2. 기획안 확인 — ㅇㅋ면 바로 PPTX로 제작합니다</h4>
      <div class="row" style="margin-bottom:10px"><button class="accent" onclick="act(${no},'approve_plan')">ㅇㅋ → 장표 제작</button></div>
      <textarea id="fb-${no}" rows="3" placeholder="기획 수정 요청"></textarea>
      <div class="row" style="margin-top:8px"><button onclick="act(${no},'plan',true)">피드백 반영해서 기획 다시</button></div></div>`;
    case "review": return `<div class="actbox"><h4>3. 장표 확인 — 보이는 그대로가 받는 PPTX입니다</h4>
      <div class="row" style="margin-bottom:10px"><button class="accent" onclick="act(${no},'approve',false,${vi})">ㅇㅋ — v${vi + 1} 확정</button>
        <a href="/api/projects/${state.pid}/slides/${no}/assets.zip"><button>에셋 zip</button></a></div>
      ${feedbackFields(no)}
      <div class="row" style="margin-top:8px"><button class="primary" onclick="act(${no},'revise',true,${vi})">수정 요청 (v${vi + 1} 기준)</button>
        <span class="muted small">빨간 펜으로 표시한 곳도 같이 보냅니다</span></div>
      <div class="upl"><span class="small">PowerPoint에서 직접 고쳤나요?</span>
        <input type="file" id="upl-${no}" accept=".pptx" style="width:auto"><button class="small" onclick="importPptx(${no})">고친 PPTX 올리기</button>
        <span class="muted small">— 새 버전이 되고, 이후 AI 수정은 이 파일 기준</span></div></div>`;
    case "done": return `<div class="actbox" style="border-color:var(--line);background:#fff"><h4>완료 — v${s.approved + 1} 확정</h4>
      <div class="row"><a href="/api/projects/${state.pid}/slides/${no}/pptx"><button class="primary">이 장표 PPTX</button></a>
      <a href="/api/projects/${state.pid}/slides/${no}/assets.zip"><button>에셋 zip</button></a>
      <button class="ghost" onclick="act(${no},'reopen')">다시 수정하기</button></div></div>`;
    case "error": return `<div class="actbox" style="border-color:var(--err);background:#FDECEA"><h4>오류</h4>
      <div class="small" style="white-space:pre-wrap">${esc(s.error)}</div>
      <div class="row" style="margin-top:8px"><button class="primary" onclick="act(${no},'retry')">다시 시도</button><button class="ghost" onclick="act(${no},'reset')">처음부터</button></div></div>`;
  }
  return "";
}

// ---------------- 액션
async function act(no, action, withFeedback, version) {
  const fd = new FormData();
  if (version != null) fd.append("version", version);
  if (withFeedback) {
    const fb = $(`#fb-${no}`)?.value.trim() || "";
    const marks = state.pen.no === no ? state.pen.marks : [];
    if (!fb && !marks.length) return toast("피드백을 적거나 빨간 펜으로 표시해 주세요.");
    fd.append("feedback", fb);
    const rule = $(`#rule-${no}`); if (rule && fb) fd.append("save_rule", rule.value);
    if (marks.length) {
      fd.append("marks", JSON.stringify(marks));
      const blob = await annotatedBlob(no); if (blob) fd.append("annotated", blob, "annotated.png");
    }
  }
  try {
    await postForm(`/api/projects/${state.pid}/slides/${no}/${action}`, fd);
    if (withFeedback) { delete state.draft[`fb-${no}`]; if (state.pen.no === no) clearPen(true); }
    delete state.verSel[no]; state.lastJson = ""; await refresh();
  } catch (e) { toast(e.message); }
}
async function importPptx(no) {
  const f = $(`#upl-${no}`).files[0]; if (!f) return toast("PPTX 파일을 선택해 주세요.");
  const fd = new FormData(); fd.append("upload", f, f.name);
  try { await postForm(`/api/projects/${state.pid}/slides/${no}/import`, fd); delete state.verSel[no]; state.lastJson = ""; refresh(); } catch (e) { toast(e.message); }
}
async function batch(nos, action) {
  const r = await post(`/api/projects/${state.pid}/batch`, { slides: nos, action });
  const bad = Object.entries(r).filter(([, v]) => v !== "ok");
  if (bad.length) toast(bad.map(([k, v]) => `${k}번: ${v}`).join("\n"));
  state.sel = String(nos[0]); state.lastJson = ""; await refresh();
}
function batchChecked(a) { batch([...state.checked].sort((x, y) => x - y), a); state.checked.clear(); }
function toggleCheck(no) { state.checked.has(no) ? state.checked.delete(no) : state.checked.add(no); render(); }
function selectSlide(no) { if (String(no) !== state.sel) clearPen(true); state.sel = String(no); state.tab = "work"; render(); }
function pickVer(no, i) { state.verSel[no] = i; clearPen(true); render(); }
function setView(no, m) { state.view[no] = m; render(); }
async function opt(no, o) { const fd = new FormData(); for (const k in o) fd.append(k, o[k]); await postForm(`/api/projects/${state.pid}/slides/${no}/options`, fd); state.lastJson = ""; refresh(); }
async function savePlan(no) {
  const fd = new FormData(); fd.append("plan", $(`#plan-${no}`).value);
  await postForm(`/api/projects/${state.pid}/slides/${no}/plan_edit`, fd); delete state.draft[`plan-${no}`]; state.lastJson = ""; refresh();
}
async function sendChat(no) {
  const inp = $(`#chatin-${no}`), msg = inp.value.trim(); if (!msg || state.chatBusy) return;
  state.chatBusy = no; delete state.draft[`chatin-${no}`]; render();
  const fd = new FormData(); fd.append("message", msg);
  try { await postForm(`/api/projects/${state.pid}/slides/${no}/chat`, fd); }
  catch (e) { toast(e.message); state.draft[`chatin-${no}`] = msg; }
  state.chatBusy = false; state.lastJson = ""; await refresh();
  const box = $(`#chat-${no}`); if (box) box.scrollTop = box.scrollHeight;
}
function chatToRevise(no, i) {
  const s = state.proj.slides[no], m = s.chat[i];
  const q = i > 0 && s.chat[i - 1].role === "user" ? `내 질문: ${s.chat[i - 1].text}\n` : "";
  const el = $(`#fb-${no}`);
  if (el) { el.value = `${q}아래 제안대로 수정:\n${m.text}`; state.draft[`fb-${no}`] = el.value; el.focus(); el.scrollIntoView({ block: "center" }); }
  else toast("장표 확인 단계에서만 수정 요청을 보낼 수 있어요.");
}

// ---------------- 빨간 펜
function togglePen(no) {
  if (state.pen.no !== no) state.pen = { no, on: true, strokes: [], marks: [] };
  else state.pen.on = !state.pen.on;
  render();
}
function clearPen(silent) { state.pen = { no: state.pen.no, on: silent ? false : state.pen.on, strokes: [], marks: [] }; if (!silent) render(); }
function setupPen(no) {
  const cv = $(`#pen-${no}`), wrap = $(`#stage-${no}`); if (!cv || !wrap) return;
  const fit = () => { const r = wrap.getBoundingClientRect(); cv.width = r.width * devicePixelRatio; cv.height = r.height * devicePixelRatio; drawPen(no); };
  new ResizeObserver(fit).observe(wrap); fit();
  let cur = null;
  const pt = e => { const r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left) / r.width * 100, y: (e.clientY - r.top) / r.height * 100 }; };
  cv.onpointerdown = e => { if (!state.pen.on) return; cv.setPointerCapture(e.pointerId); cur = [pt(e)]; state.pen.strokes.push(cur); };
  cv.onpointermove = e => { if (cur) { cur.push(pt(e)); drawPen(no); } };
  cv.onpointerup = () => {
    if (!cur) return;
    const xs = cur.map(p => p.x), ys = cur.map(p => p.y);
    const m = { x: Math.max(0, Math.min(...xs)), y: Math.max(0, Math.min(...ys)) };
    m.w = Math.max(1, Math.max(...xs) - m.x); m.h = Math.max(1, Math.max(...ys) - m.y);
    state.pen.marks.push(m); cur = null; drawPen(no);
    const c = $(`#pencount-${no}`); if (c) c.textContent = `표시 ${state.pen.marks.length}곳`;
  };
}
function drawPen(no) {
  const cv = $(`#pen-${no}`); if (!cv || state.pen.no !== no) return;
  const g = cv.getContext("2d"); g.clearRect(0, 0, cv.width, cv.height);
  paintStrokes(g, cv.width, cv.height);
}
function paintStrokes(g, W, H) {
  g.strokeStyle = "#E5231B"; g.lineWidth = Math.max(3, W / 300); g.lineCap = g.lineJoin = "round";
  for (const s of state.pen.strokes) { g.beginPath(); s.forEach((p, i) => (i ? g.lineTo : g.moveTo).call(g, p.x / 100 * W, p.y / 100 * H)); g.stroke(); }
  g.fillStyle = "#E5231B"; g.font = `bold ${Math.max(14, W / 60)}px Pretendard, sans-serif`;
  state.pen.marks.forEach((m, i) => g.fillText(String(i + 1), m.x / 100 * W, Math.max(14, m.y / 100 * H - 4)));
}
async function annotatedBlob(no) {
  const img = $(`#shot-${no}`); if (!img || !img.complete) return null;   // 실제 렌더 이미지 위에 합성 (브라우저 미리보기는 좌표만 전송)
  const c = document.createElement("canvas"); c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d"); g.drawImage(img, 0, 0); paintStrokes(g, c.width, c.height);
  return new Promise(r => c.toBlob(r, "image/png"));
}

// ---------------- 브라우저 미리보기 (레이아웃 JSON → SVG)
async function getLayout(rel) {
  if (!state.layouts[rel]) state.layouts[rel] = await api(fileUrl(rel));
  return state.layouts[rel];
}
function numFmt(v, f) {
  if (!f || f === "General") return String(+(+v).toFixed(2));
  const m = f.match(/^0(?:\.(0+))?(?:"(.*)")?$/); if (!m) return String(v);
  return (+v).toFixed(m[1] ? m[1].length : 0) + (m[2] || "");
}
function imgHref(el, s, v) {
  const src = String(el.source || "");
  if (v.images && v.images[el.id]) return fileUrl(v.images[el.id]);
  if (src.startsWith("gen:")) return fileUrl(`slides/${String(s.no).padStart(2, "0")}/assets/${src.slice(4)}.png`);
  if (src.startsWith("user:")) return fileUrl(`assets/${src.slice(5)}`);
  if (src.startsWith("file:")) return fileUrl(src.slice(5));
  return null;
}
function layoutSVG(L, s, v) {
  const [W, H] = state.proj.slide_size_pt || [960, 540];
  const X = p => p / 100 * W, Y = p => p / 100 * H, out = [];
  const idTag = el => state.showIds ? `<text x="${X(el.x ?? el.x1) + 2}" y="${Y(el.y ?? el.y1) + 9}" font-size="8" fill="#E5231B" font-family="monospace">${esc(el.id)}</text>` : "";
  for (const el of L.elements || []) {
    const x = X(el.x || 0), y = Y(el.y || 0), w = X(el.w || 0), h = Y(el.h || 0);
    const t = el.type;
    if (t === "text") {
      const runs = el.runs || [{ text: el.text || "" }];
      const html = runs.map(r => `<span style="color:${esc(r.color || el.color || "#111")};font-weight:${(r.bold ?? el.bold) ? 700 : 400};font-size:${r.size || el.size || 14}px">${esc(r.text || "")}</span>`).join("");
      const jc = { middle: "center", bottom: "flex-end" }[el.valign] || "flex-start";
      out.push(`<foreignObject x="${x}" y="${y}" width="${w}" height="${h}" style="overflow:visible"><div xmlns="http://www.w3.org/1999/xhtml" style="width:${w}px;height:${h}px;display:flex;flex-direction:column;justify-content:${jc};font-family:Pretendard,sans-serif;font-size:${el.size || 14}px;line-height:${el.line_spacing || 1.2};text-align:${el.align || "left"};white-space:pre-wrap;word-break:keep-all;overflow-wrap:anywhere;letter-spacing:-0.01em"><div>${html}</div></div></foreignObject>`);
    } else if (t === "rect" || t === "ellipse" || (t === "image" && !imgHref(el, s, v))) {
      const ph = t === "image";
      const fill = ph ? "#F3F3F1" : (el.fill || "none"), line = ph ? "#E7E7E4" : (el.line || "none");
      const rr = t === "rect" ? (el.radius || 0) * Math.min(w, h) : 0;
      out.push(t === "ellipse" ? `<ellipse cx="${x + w / 2}" cy="${y + h / 2}" rx="${w / 2}" ry="${h / 2}" fill="${fill}" stroke="${line}" stroke-width="${el.line_w || 0.75}"/>`
        : `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rr}" fill="${fill}" stroke="${line}" stroke-width="${ph ? 0.75 : el.line_w || 0.75}"/>`);
      const label = ph ? (el.label || "이미지 자리") : el.text;
      if (label) out.push(`<foreignObject x="${x}" y="${y}" width="${w}" height="${h}"><div xmlns="http://www.w3.org/1999/xhtml" style="width:${w}px;height:${h}px;display:flex;align-items:center;justify-content:center;text-align:center;font-family:Pretendard,sans-serif;font-size:${ph ? 10 : el.size || 12}px;color:${ph ? "#747474" : el.color || "#111"};font-weight:${el.bold ? 700 : 400}">${esc(label)}</div></foreignObject>`);
    } else if (t === "line") {
      out.push(`<line x1="${X(el.x1)}" y1="${Y(el.y1)}" x2="${X(el.x2)}" y2="${Y(el.y2)}" stroke="${el.color || "#111"}" stroke-width="${el.width || 1}" ${el.dash ? `stroke-dasharray="${(el.width || 1) * 4} ${(el.width || 1) * 3}"` : ""} ${el.arrow ? `marker-end="url(#arrow-${el.color ? el.color.replace("#", "") : "111111"})"` : ""}/>`);
    } else if (t === "donut") {
      const vals = el.values || [1], tot = vals.reduce((a, b) => a + (+b), 0) || 1;
      const R = Math.min(w, h) / 2, r = R * (el.hole || 0.68), mid = (R + r) / 2, C = 2 * Math.PI * mid, cx = x + w / 2, cy = y + h / 2;
      let off = 0; const cols = el.colors || ["#FF6B2C", "#E7E7E4"];
      vals.forEach((val, i) => { const len = C * (+val) / tot;
        out.push(`<circle cx="${cx}" cy="${cy}" r="${mid}" fill="none" stroke="${cols[i % cols.length]}" stroke-width="${R - r}" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 ${cx} ${cy})"/>`); off += len; });
    } else if (t === "bar") {
      const cats = el.categories || [], vals = (el.values || []).map(Number), mx = Math.max(...vals, 0) || 1, n = vals.length || 1;
      const cols = el.colors || ["#C9C9C6", "#FF6B2C"], ls = el.label_size || 11, vs = el.value_size || 12;
      if (el.horizontal) {
        const lab = w * 0.22, room = w - lab - vs * 3.5, slot = h / n, bh = slot / (1 + (el.gap || 70) / 100);
        vals.forEach((val, i) => { const by = y + h - (i + 1) * slot + (slot - bh) / 2, bw = room * val / mx;   // PowerPoint 가로막대는 첫 항목이 아래
          out.push(`<text x="${x + lab - 6}" y="${by + bh / 2 + ls / 3}" font-size="${ls}" text-anchor="end" fill="#747474" font-family="Pretendard">${esc(cats[i] ?? "")}</text>`,
            `<rect x="${x + lab}" y="${by}" width="${bw}" height="${bh}" fill="${cols[i % cols.length]}"/>`,
            `<text x="${x + lab + bw + 5}" y="${by + bh / 2 + vs / 3}" font-size="${vs}" font-weight="700" fill="#111" font-family="Pretendard">${esc(numFmt(val, el.number_format))}</text>`); });
      } else {
        const lab = ls * 2, room = h - lab - vs * 2, slot = w / n, bw = slot / (1 + (el.gap || 70) / 100);
        vals.forEach((val, i) => { const bx = x + i * slot + (slot - bw) / 2, bh = room * val / mx, by = y + h - lab - bh;
          out.push(`<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" fill="${cols[i % cols.length]}"/>`,
            `<text x="${bx + bw / 2}" y="${by - 5}" font-size="${vs}" font-weight="700" text-anchor="middle" fill="#111" font-family="Pretendard">${esc(numFmt(val, el.number_format))}</text>`,
            `<text x="${bx + bw / 2}" y="${y + h - lab / 3}" font-size="${ls}" text-anchor="middle" fill="#747474" font-family="Pretendard">${esc(cats[i] ?? "")}</text>`); });
      }
    } else if (t === "image") {
      out.push(`<image href="${imgHref(el, s, v)}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="${el.fit === "stretch" ? "none" : "xMidYMid meet"}"/>`);
    }
    out.push(idTag(el));
  }
  const colors = [...new Set((L.elements || []).filter(e => e.type === "line" && e.arrow).map(e => (e.color || "#111111").replace("#", "")))];
  const defs = colors.map(c => `<marker id="arrow-${c}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#${c}"/></marker>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;display:block;background:${esc(L.background || "#fff")}"><defs>${defs}</defs>${out.join("")}</svg>`;
}
async function afterRender() {
  const s = state.proj && state.proj.slides[state.sel];
  if (!s || state.tab !== "work") return;
  const box = $(`#svg-${s.no}`);
  if (box) {
    try { const v = s.versions[verSel(s)]; box.innerHTML = layoutSVG(await getLayout(box.dataset.layout), s, v); }
    catch (e) { box.innerHTML = `<div class="empty" style="height:100%">미리보기 실패: ${esc(e.message)}</div>`; }
  }
  setupPen(s.no);
  const cb = $(`#chat-${s.no}`); if (cb) cb.scrollTop = cb.scrollHeight;
}

// ---------------- 내용 파악 / 가이드 / 자료 탭
function renderSummary() {
  const p = state.proj;
  $("#tab").innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><h3>덱 내용 파악</h3>
    <button onclick="post('/api/projects/${p.id}/summary').then(()=>{state.lastJson='';refresh()}).catch(e=>toast(e.message))">다시 파악</button></div>
    ${p.ingest.message ? `<div class="warns">${esc(p.ingest.message)}</div>` : ""}
    <div class="md">${esc(p.summary) || '<span class="muted">아직 없음</span>'}</div></div>`;
}
function renderGuide() {
  const p = state.proj;
  $("#tab").innerHTML = `<div class="card">
    <div class="row" style="justify-content:space-between"><h3>디자인 가이드 <span class="muted small">— 모든 기획/제작/수정에 자동 적용</span></h3>
      <div class="row"><button onclick="post('/api/projects/${p.id}/guide/build').then(()=>{state.lastJson='';refresh()}).catch(e=>toast(e.message))">레퍼런스 + 확정 장표 보고 AI가 가이드 작성</button>
      <button class="primary" onclick="saveGuide()">저장</button></div></div>
    <textarea id="guide" rows="30" style="font-family:ui-monospace,Consolas,monospace;font-size:13px">${esc(p.guide)}</textarea>
    ${p.rules_log?.length ? `<h3 style="margin-top:14px">피드백에서 쌓인 규칙 (${p.rules_log.length})</h3><ul class="rules">${p.rules_log.map((r, i) =>
      `<li>${esc(r)} <button class="ghost small" onclick="delRule(${i})">삭제</button></li>`).join("")}</ul>` : ""}</div>`;
}
async function delRule(i) { await post(`/api/projects/${state.pid}/rules/delete`, { rule: state.proj.rules_log[i] }); delete state.draft.guide; state.lastJson = ""; refresh(); }
async function saveGuide() { await post(`/api/projects/${state.pid}/guide`, { text: $("#guide").value }); delete state.draft.guide; state.lastJson = ""; refresh(); }

function renderFiles() {
  const p = state.proj;
  const thumbs = arr => `<div class="thumbs">${arr.map(f => `<div class="thumb"><a href="${fileUrl(f)}" target="_blank"><img src="${fileUrl(f)}"></a>
    <button onclick="delFile('${esc(f)}')">✕</button></div>`).join("") || '<span class="muted small">없음</span>'}</div>`;
  const up = (kind, label, accept) => `<div class="row" style="margin-top:8px"><input type="file" id="up-${kind}" multiple accept="${accept}" style="width:auto">
    <button onclick="upload('${kind}')">${label}</button></div>`;
  $("#tab").innerHTML = `<div class="grid">
    <div class="card"><h3>요청사항 / 톤앤매너</h3><textarea id="req" rows="4">${esc(p.request)}</textarea>
      <div class="row" style="margin-top:6px"><button onclick="saveReq()">저장</button></div></div>
    <div class="card"><h3>원본 덱</h3><div class="muted small">${p.sources.map(esc).join(", ") || "없음"} · 장표 ${p.pages.length}장
      ${p.template ? " · <b>원본 PPTX 템플릿 사용 중</b> (테마·마스터 유지, 전체 덱은 원본에서 완료 장표만 교체)" : " · PPTX로 올리면 원본 테마를 유지할 수 있어요"}</div>
      ${up("source", "올리기 (다시 분석)", ".pdf,.pptx,.png,.jpg,.jpeg,.webp")}</div>
    <div class="card"><h3>레퍼런스 이미지 <span class="muted small">— 톤앤매너 기준. 기획/제작에 자동 첨부</span></h3>${thumbs(p.refs)}${up("refs", "추가", "image/*")}</div>
    <div class="card"><h3>실제 에셋 <span class="muted small">— 로고·캐릭터·스크린샷·굿즈. 장표에 그대로 사용</span></h3>${thumbs(p.user_assets)}${up("assets", "추가", "image/*")}</div>
  </div>`;
}
async function upload(kind) {
  const inp = $(`#up-${kind}`); if (!inp.files.length) return;
  const fd = new FormData(); fd.append("kind", kind); for (const f of inp.files) fd.append("files", f);
  try { await postForm(`/api/projects/${state.pid}/upload`, fd); state.lastJson = ""; refresh(); } catch (e) { toast(e.message); }
}
async function delFile(path) { if (!confirm("삭제할까요?")) return; await post(`/api/projects/${state.pid}/delete_file`, { path }); state.lastJson = ""; refresh(); }
async function saveReq() { await post(`/api/projects/${state.pid}/meta`, { request: $("#req").value }); delete state.draft.req; state.lastJson = ""; refresh(); }

// ---------------- 입력 중인 내용 보존 (자동 갱신 시)
document.addEventListener("input", e => { if (e.target.id && e.target.tagName !== "SELECT" && !["file", "checkbox"].includes(e.target.type)) state.draft[e.target.id] = e.target.value; });
document.addEventListener("focusin", e => { state.focusId = e.target.id || null; });
function restoreDrafts() {
  for (const [id, v] of Object.entries(state.draft)) { const el = document.getElementById(id); if (el) el.value = v; }
  if (state.focusId) { const el = document.getElementById(state.focusId); if (el && el.tagName === "TEXTAREA") { el.focus(); el.selectionStart = el.selectionEnd = el.value.length; } }
}

// ---------------- 설정
async function loadSettings() {
  state.settings = await api("/api/settings");
  const b = $("#mockBadge"), s = state.settings;
  b.hidden = !(s.mock || s.mode === "manual");
  b.textContent = s.mock ? "데모 모드" : "복붙 모드";
  b.className = "badge " + (s.mock ? "warn" : "user");
}
async function openSettings() {
  await loadSettings();
  const f = $("#settings form");
  for (const [k, v] of Object.entries(state.settings)) {
    const el = f.elements[k]; if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else if (k !== "api_key") el.value = v;
  }
  f.elements.api_key.placeholder = state.settings.api_key ? `저장됨 (${state.settings.api_key}) — 변경할 때만 입력` : "sk-...";
  f.elements.api_key.value = "";
  $("#settings").showModal();
}
async function saveSettings(e) {
  e.preventDefault();
  const f = e.target, body = {};
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") body[el.name] = el.checked;
    else if (el.name === "api_key") { if (el.value.trim()) body.api_key = el.value.trim(); }
    else if (el.type === "number") body[el.name] = Number(el.value);
    else body[el.name] = el.value;
  }
  await post("/api/settings", body); await loadSettings(); $("#settings").close(); if (state.proj) render();
}

// ---------------- 시작
(async () => {
  await loadSettings();
  try { state.env = await api("/api/env"); } catch { }
  if (!state.settings.api_key && !state.settings.mock && state.settings.mode !== "manual") setTimeout(openSettings, 300);
  await route();
  setInterval(() => { if (state.pid && document.visibilityState === "visible" && !(state.pen.on && state.pen.strokes.length)) refresh(); }, 2000);
})();
