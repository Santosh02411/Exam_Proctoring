<?php
session_start();
if (!isset($_SESSION['user_id'])) die("Login required");
$uid = (int)$_SESSION['user_id'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// load student info
$ustmt = $conn->prepare("SELECT id, name, email, avatar, created_at FROM users WHERE id = ? LIMIT 1");
$ustmt->bind_param("i", $uid);
$ustmt->execute();
$user = $ustmt->get_result()->fetch_assoc();
$ustmt->close();
$student_name = $user['name'] ?? 'Student';
$student_email = $user['email'] ?? '';
$student_avatar = $user['avatar'] ?? null;

// counts / stats
$countAssignedStmt = $conn->prepare("SELECT COUNT(*) AS c FROM test_eligibility WHERE student_id = ?");
$countAssignedStmt->bind_param("i", $uid);
$countAssignedStmt->execute();
$total_assigned = (int)$countAssignedStmt->get_result()->fetch_assoc()['c'];
$countAssignedStmt->close();

$nowSql = date('Y-m-d H:i:s');
$upcomingStmt = $conn->prepare("SELECT COUNT(*) AS c FROM tests t JOIN test_eligibility e ON e.test_id=t.id WHERE e.student_id = ? AND t.start_time IS NOT NULL AND t.start_time > ?");
$upcomingStmt->bind_param("is", $uid, $nowSql);
$upcomingStmt->execute();
$upcoming_count = (int)$upcomingStmt->get_result()->fetch_assoc()['c'];
$upcomingStmt->close();

$activeStmt = $conn->prepare("SELECT COUNT(*) AS c FROM tests t JOIN test_eligibility e ON e.test_id=t.id WHERE e.student_id = ? AND (t.status='published' OR t.status='draft') AND (t.end_time IS NULL OR t.end_time > ?)");
$activeStmt->bind_param("is", $uid, $nowSql);
$activeStmt->execute();
$active_count = (int)$activeStmt->get_result()->fetch_assoc()['c'];
$activeStmt->close();

// fetch assigned tests
$sql = "SELECT t.* FROM tests t
        JOIN test_eligibility e ON e.test_id = t.id
        WHERE e.student_id = ? 
        ORDER BY COALESCE(t.start_time, '9999-12-31') ASC, t.created_at DESC";
$stmt = $conn->prepare($sql);
$stmt->bind_param("i", $uid);
$stmt->execute();
$res = $stmt->get_result();

// prepare JS data array for client side searching/filtering (will be rendered safely)
$tests = [];
while ($row = $res->fetch_assoc()) {
    $tests[] = $row;
}
$stmt->close();
$conn->close();
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Dashboard — Available Tests</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg: #f5f7fb;
      --card: #ffffff;
      --muted: #6b7280;
      --primary: #0b6ef6;
      --accent: #7c3aed;
      --danger: #ef4444;
      --success: #10b981;
      --radius: 12px;
      --shadow: 0 12px 30px rgba(11,22,50,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    }
    *{box-sizing:border-box}
    body{ margin:0; background:linear-gradient(180deg,var(--bg),#eef3fb); color:#071033; -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; padding:26px; }

    .container{ max-width:1200px; margin:0 auto; display:grid; gap:18px; }

    header.top {
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:12px;
    }
    .user {
      display:flex;
      gap:12px;
      align-items:center;
    }
    .avatar {
      width:56px;
      height:56px;
      border-radius:12px;
      background:linear-gradient(135deg,var(--primary),var(--accent));
      color:#fff;
      display:flex;
      align-items:center;
      justify-content:center;
      font-weight:700;
      font-size:18px;
      box-shadow: 0 8px 20px rgba(11,22,50,0.08);
    }
    .userinfo { line-height:1; }
    .userinfo .name { font-weight:800; font-size:16px; }
    .userinfo .email { font-size:13px; color:var(--muted); }

    nav.controls {
      display:flex;
      align-items:center;
      gap:12px;
    }
    .search {
      display:flex;
      gap:8px;
      align-items:center;
      background:#fff;
      padding:8px 10px;
      border-radius:10px;
      box-shadow:var(--shadow);
      border:1px solid rgba(11,110,246,0.04);
      min-width:300px;
    }
    .search input { border:0; outline:0; width:100%; font-size:14px; background:transparent; }

    .stats {
      display:flex;
      gap:12px;
      align-items:center;
    }
    .stat {
      background:var(--card);
      padding:10px 14px;
      border-radius:10px;
      box-shadow:var(--shadow);
      display:flex;
      gap:10px;
      align-items:center;
      min-width:130px;
    }
    .stat strong { display:block; font-size:18px; }
    .stat span { display:block; font-size:12px; color:var(--muted); }

    main { display:grid; grid-template-columns: 1fr 320px; gap:18px; align-items:start; }
    @media (max-width:980px){ main{ grid-template-columns:1fr } .search{ min-width:160px; } }

    .panel { background:var(--card); border-radius:12px; padding:14px; box-shadow:var(--shadow); }
    .tests-grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap:12px; }

    .test-card {
      background:linear-gradient(180deg,#fff,#fbfdff);
      border-radius:12px;
      padding:14px;
      box-shadow:0 8px 24px rgba(11,22,50,0.04);
      display:flex;
      flex-direction:column;
      gap:10px;
      min-height:150px;
      transition:transform .14s ease, box-shadow .14s ease;
    }
    .test-card:hover { transform:translateY(-6px); box-shadow:0 18px 40px rgba(11,22,50,0.08); }
    .test-title { font-weight:800; font-size:15px; color:#071033; }
    .test-desc { color:var(--muted); font-size:13px; min-height:40px; overflow:hidden; }
    .meta-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:13px; color:var(--muted); }
    .pill { padding:6px 10px; border-radius:999px; background:#eef6ff; color:var(--primary); font-weight:700; font-size:13px; }

    .card-footer { margin-top:auto; display:flex; justify-content:space-between; align-items:center; gap:8px; }
    .btn {
      padding:9px 12px; border-radius:10px; border:0; cursor:pointer; font-weight:800; color:#fff; background:var(--primary);
      box-shadow:0 8px 20px rgba(11,110,246,0.12);
    }
    .btn.ghost { background:transparent; color:var(--primary); border:1px solid rgba(11,110,246,0.08); box-shadow:none; }
    .btn.disabled { background:#cbd5e1; color:#fff; cursor:not-allowed; box-shadow:none; opacity:0.7; }

    .side .panel section { margin-bottom:12px; }
    .small { font-size:13px; color:var(--muted); }

    .empty { padding:24px; text-align:center; color:var(--muted); border-radius:12px; background:var(--card); box-shadow:var(--shadow); }

    /* tooltip style for disabled reason */
    .disabled-tip { font-size:13px; color:var(--muted); }

  </style>
</head>
<body>
  <div class="container">
    <header class="top">
      <div class="user">
        <?php if ($student_avatar): ?>
          <img src="<?= htmlspecialchars($student_avatar) ?>" alt="avatar" style="width:56px;height:56px;border-radius:12px;object-fit:cover;box-shadow:var(--shadow)">
        <?php else: 
          // initials
          $initials = implode('', array_slice(array_filter(explode(' ', $student_name)), 0, 2));
          if (!$initials) $initials = strtoupper(substr($student_email,0,2) ?: 'ST');
        ?>
          <div class="avatar"><?= htmlspecialchars(strtoupper($initials)) ?></div>
        <?php endif; ?>
        <div class="userinfo">
          <div class="name"><?= htmlspecialchars($student_name) ?></div>
          <div class="email"><?= htmlspecialchars($student_email) ?></div>
        </div>
      </div>

      <nav class="controls">
        <div class="search" role="search" aria-label="Search tests">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style="opacity:.6"><path d="M21 21l-4.35-4.35" stroke="#94a3b8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#94a3b8" stroke-width="1.6"/></svg>
          <input id="searchInput" type="search" placeholder="Search by title or description...">
        </div>

        <div class="stats">
          <div class="stat"><strong><?= $total_assigned ?></strong><span>Assigned</span></div>
          <div class="stat"><strong><?= $active_count ?></strong><span>Active</span></div>
          <div class="stat"><strong><?= $upcoming_count ?></strong><span>Upcoming</span></div>
        </div>
      </nav>
    </header>

    <main>
      <section>
        <div class="panel">
          <h3 style="margin:0 0 8px 0">Your Tests</h3>
          <div class="small">Tests assigned to you. Click <strong>Start</strong> to begin (Start will be disabled if test window hasn't started or is closed).</div>

          <div style="margin-top:12px" id="testsArea">
            <?php if (count($tests) === 0): ?>
              <div class="empty">No tests assigned to you right now. Check back later or contact your instructor.</div>
            <?php else: ?>
              <div class="tests-grid" id="testsGrid">
                <!-- Cards will be rendered by JS for dynamic filtering -->
              </div>
            <?php endif; ?>
          </div>
        </div>
      </section>

      <aside class="side">
        <div class="panel">
          <h4 style="margin:0 0 8px 0">Quick Links</h4>
          <section>
            <a class="btn ghost" href="student_dashboard.php">Dashboard</a>
          </section>

          <h4 style="margin:8px 0 8px 0">How it works</h4>
          <p class="small">• Start is enabled only within the test's start/end window (if set).<br>• Tests marked draft cannot be started. <br>• Your session is monitored for integrity when you start a proctored test.</p>
        </div>

        <div class="panel" aria-live="polite">
          <h4 style="margin:0 0 8px 0">Summary</h4>
          <div class="small">Assigned tests: <strong><?= $total_assigned ?></strong></div>
          <div class="small">Active (open) tests: <strong><?= $active_count ?></strong></div>
          <div class="small">Upcoming (future start): <strong><?= $upcoming_count ?></strong></div>
        </div>
      </aside>
    </main>
  </div>

<script>
  // Data injected from PHP (escaped)
  const TESTS = <?= json_encode($tests, JSON_HEX_TAG|JSON_HEX_APOS|JSON_HEX_QUOT|JSON_HEX_AMP) ?>;
  const now = new Date();

  function parseServerDatetime(s) {
    if (!s) return null;
    // server format "YYYY-MM-DD HH:MM:SS"
    const iso = s.replace(' ', 'T');
    const d = new Date(iso);
    if (!isNaN(d)) return d;
    // fallback try with timezone Z
    const d2 = new Date(iso + 'Z');
    return isNaN(d2) ? null : d2;
  }

  // render a single card (returns DOM element)
  function renderCard(t) {
    const card = document.createElement('article');
    card.className = 'test-card';
    card.dataset.testId = t.id;

    // Title & desc
    const title = document.createElement('div');
    title.className = 'test-title';
    title.textContent = t.title || 'Untitled Test';
    const desc = document.createElement('div');
    desc.className = 'test-desc';
    desc.innerHTML = (t.description ? escapeHtml(t.description).replace(/\n/g,'<br>') : '<span class="small">No description provided.</span>');

    // meta row
    const meta = document.createElement('div');
    meta.className = 'meta-row';
    const dur = document.createElement('div');
    dur.className = 'pill';
    dur.textContent = (t.duration_minutes ? (t.duration_minutes + ' min') : '—');
    meta.appendChild(dur);

    const status = document.createElement('div');
    status.className = 'small';
    status.innerHTML = 'Status: <strong>' + (t.status || 'unknown') + '</strong>';
    meta.appendChild(status);

    if (t.start_time) {
      const s = document.createElement('div'); s.className='small'; s.innerHTML = 'Start: <strong>' + t.start_time + '</strong>'; meta.appendChild(s);
    }
    if (t.end_time) {
      const e = document.createElement('div'); e.className='small'; e.innerHTML = 'End: <strong>' + t.end_time + '</strong>'; meta.appendChild(e);
    }

    // footer with actions
    const footer = document.createElement('div');
    footer.className = 'card-footer';
    const left = document.createElement('div'); left.className='small muted';
    left.textContent = 'Questions: ' + (t.total_questions || '—');

    const right = document.createElement('div');

    const viewBtn = document.createElement('a');
    viewBtn.className = 'btn ghost';
    viewBtn.href = 'v_test.php?test_id=' + encodeURIComponent(t.id);
    viewBtn.textContent = 'View';

    const startBtn = document.createElement('a');
    startBtn.className = 'btn';
    startBtn.href = 'start_test.php?test_id=' + encodeURIComponent(t.id);
    startBtn.textContent = 'Start';

    // determine enable/disable with reasons
    let enabled = true;
    let reason = '';
    const startDT = parseServerDatetime(t.start_time);
    const endDT = parseServerDatetime(t.end_time);
    if (t.status && t.status.toLowerCase().includes('draft')) {
      enabled = false; reason = 'Test is in draft';
    }
    if (startDT && now < startDT) { enabled = false; reason = 'Not started yet'; }
    if (endDT && now > endDT) { enabled = false; reason = 'Test window closed'; }

    if (!enabled) {
      startBtn.classList.add('disabled');
      startBtn.setAttribute('aria-disabled','true');
      // override click
      startBtn.addEventListener('click', function(ev){ ev.preventDefault(); alert(reason + '. You cannot start this test now.'); });
      // small reason label
      const r = document.createElement('div'); r.className='disabled-tip small'; r.textContent = reason; footer.appendChild(r);
    } else {
      // attach normal handler to open start page (could include consent overlay there)
      startBtn.addEventListener('click', function(){ /* normal navigation */ });
    }

    // assemble
    right.appendChild(viewBtn);
    right.appendChild(startBtn);
    footer.appendChild(left);
    footer.appendChild(right);

    card.appendChild(title);
    card.appendChild(desc);
    card.appendChild(meta);
    card.appendChild(footer);

    return card;
  }

  // escapeHtml helper
  function escapeHtml(s) {
    return (s||'').replace(/[&<>"']/g, function(m){ return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]); });
  }

  // initial render
  const grid = document.getElementById('testsGrid');
  function renderAll(list) {
    if (!grid) return;
    grid.innerHTML = '';
    list.forEach(t => {
      grid.appendChild(renderCard(t));
    });
  }

  // search input
  const searchInput = document.getElementById('searchInput');
  function performSearch() {
    const q = (searchInput.value || '').trim().toLowerCase();
    if (!q) {
      renderAll(TESTS);
      return;
    }
    const filtered = TESTS.filter(t => {
      return (t.title && t.title.toLowerCase().includes(q)) ||
             (t.description && t.description.toLowerCase().includes(q)) ||
             (t.test_id && (t.test_id + '').toLowerCase().includes(q));
    });
    renderAll(filtered);
  }

  // init
  document.addEventListener('DOMContentLoaded', function(){
    // populate grid
    if (TESTS.length > 0) renderAll(TESTS);

    // wire search
    searchInput.addEventListener('input', function(){
      performSearch();
    });
  });
</script>
</body>
</html>
