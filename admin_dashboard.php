<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') {
    header("Location: login.html");
    exit;
}

$uid = $_SESSION['user_id'];
$name = $_SESSION['name'];

$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

// Load some quick stats
$total_tests = $conn->query("SELECT COUNT(*) AS c FROM tests")->fetch_assoc()['c'];
$total_students = $conn->query("SELECT COUNT(*) AS c FROM users WHERE role='student'")->fetch_assoc()['c'];
$total_admins = $conn->query("SELECT COUNT(*) AS c FROM users WHERE role='admin'")->fetch_assoc()['c'];
$recent_tests = $conn->query("SELECT id, title, status, created_at FROM tests ORDER BY created_at DESC LIMIT 6");
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Admin Dashboard — Projectech</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f6f8fb;
      --card:#ffffff;
      --muted:#65748b;
      --primary:#0b6ef6;
      --accent:#7c3aed;
      --success:#10b981;
      --danger:#ef4444;
      --glass: rgba(255,255,255,0.6);
      --radius:14px;
      --shadow-lg: 0 12px 30px rgba(15,23,42,0.08);
      --shadow-sm: 0 6px 18px rgba(15,23,42,0.06);
      font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      background:linear-gradient(180deg,var(--bg),#f3f6fa);
      color:#0b1220;
      -webkit-font-smoothing:antialiased;
      -moz-osx-font-smoothing:grayscale;
      padding:28px;
    }

    /* Header */
    header {
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:16px;
      margin-bottom:22px;
    }
    .brand {
      display:flex;
      gap:14px;
      align-items:center;
    }
    .logo {
      width:56px;height:56px;border-radius:12px;
      background:linear-gradient(135deg,var(--primary),var(--accent));
      display:grid;place-items:center;color:#fff;font-weight:800;
      box-shadow:var(--shadow-lg);
      font-size:20px;
    }
    .brand h1{margin:0;font-size:18px;letter-spacing:0.2px}
    .brand p{margin:0;color:var(--muted);font-size:13px}

    .header-right { display:flex; gap:12px; align-items:center; }
    .search {
      display:flex; align-items:center; gap:8px;
      background:var(--card); padding:10px 12px; border-radius:12px; box-shadow:var(--shadow-sm);
      min-width:300px;
    }
    .search input{
      border:0; outline:0; font-size:14px; width:100%;
      color:#0b1220; background:transparent;
    }

    .user {
      display:flex; align-items:center; gap:10px;
      padding:6px 10px; border-radius:999px; background:var(--card); box-shadow:var(--shadow-sm);
    }
    .avatar {
      width:40px;height:40px;border-radius:10px;background:#eef2ff;color:var(--primary);
      display:grid;place-items:center;font-weight:700;
    }
    .quick-actions { display:flex; gap:10px; align-items:center; }
    .btn {
      display:inline-flex; gap:8px; align-items:center; padding:10px 14px; border-radius:10px;
      background:var(--primary); color:#fff; text-decoration:none; font-weight:600; font-size:14px;
      box-shadow: 0 8px 20px rgba(11,110,246,0.14);
    }
    .btn.ghost { background:transparent; color:var(--primary); border:1px solid rgba(11,110,246,0.12); box-shadow:none }
    .btn.alt { background:#111827; color:#fff }

    /* Stats */
    .stats { display:grid; grid-template-columns: repeat(3, 1fr); gap:18px; margin-bottom:20px; }
    @media (max-width:940px){ .stats{ grid-template-columns: repeat(2, 1fr);} }
    @media (max-width:600px){ .stats{ grid-template-columns: 1fr; } header{flex-direction:column;align-items:flex-start} .header-right{width:100%;justify-content:space-between} .search{min-width:160px} }
    .card {
      background:var(--card); border-radius:var(--radius); padding:18px; box-shadow:var(--shadow-lg);
      display:flex; flex-direction:column; gap:8px;
    }
    .card h3{margin:0;font-size:13px;color:var(--muted)}
    .card .value{font-size:28px;font-weight:800}
    .card .sub{color:var(--muted);font-size:13px}

    /* Table area */
    .panel { display:grid; grid-template-columns: 1fr 320px; gap:18px; align-items:start; }
    @media (max-width:980px){ .panel{ grid-template-columns: 1fr; } }

    .table-wrap{ background:transparent; }
    table { width:100%; border-collapse:collapse; background:transparent; }
    thead th { text-align:left; font-size:13px; color:var(--muted); padding:12px 14px; }
    tbody td { background:var(--card); padding:14px; border-radius:10px; margin-bottom:8px; vertical-align:top }
    tbody tr + tr td { margin-top:8px } /* visual spacing handled via display:block rows below */

    /* We'll style rows as cards for a classy feel on narrow screens */
    .table-list { display:grid; gap:12px; }
    .test-row { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:12px; border-radius:10px; box-shadow:var(--shadow-sm); background:var(--card) }
    .test-left { display:flex; gap:12px; align-items:flex-start; max-width:70% }
    .test-meta { color:var(--muted); font-size:13px }
    .badge { display:inline-block; padding:6px 10px; border-radius:999px; font-weight:700; font-size:12px; }
    .badge.published { background:rgba(16,185,129,0.12); color:var(--success) }
    .badge.draft { background:rgba(99,102,241,0.08); color:var(--accent) }

    .actions { display:flex; gap:8px; align-items:center }
    .action-link { display:inline-flex; gap:8px; align-items:center; padding:8px 10px; border-radius:8px; text-decoration:none; font-weight:600; color:var(--primary); background:transparent }
    .action-link:hover{ background:rgba(11,110,246,0.04) }

    /* Quick panel */
    .side-card { display:flex; flex-direction:column; gap:12px; }
    .side-card a { text-decoration:none; padding:10px 12px; border-radius:10px; background:linear-gradient(90deg,#ffffff,#fbfdff); box-shadow:var(--shadow-sm); color:var(--primary); font-weight:700 }
    .side-card .help { color:var(--muted); font-size:13px }

    /* small helpers */
    .muted{ color:var(--muted); font-size:13px }
    .nowrap{ white-space:nowrap; }

    /* search empty state */
    .empty { text-align:center; color:var(--muted); padding:28px; }

    /* subtle focus outlines */
    a:focus, button:focus, input:focus { outline: 3px solid rgba(11,110,246,0.12); outline-offset:2px; border-radius:8px }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo" aria-hidden>PT</div>
      <div>
        <h1>Projectech Admin</h1>
        <p>Welcome back, <strong><?= htmlspecialchars($name) ?></strong></p>
      </div>
    </div>

    <div class="header-right">
      <div class="search" role="search" aria-label="Search tests">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M21 21l-4.35-4.35" stroke="#94a3b8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#94a3b8" stroke-width="1.6"/></svg>
        <input id="tableSearch" type="search" placeholder="Search tests by title..." aria-label="Search tests">
      </div>

      <div class="quick-actions">
        <a class="btn" href="create_test.php" title="Create Test">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M12 5v14M5 12h14" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>
          Create Test
        </a>
        <a class="btn ghost" href="assign_students.php" title="Assign Students">Assign</a>
        <div class="user" title="Logged in user">
          <div class="avatar" aria-hidden><?= strtoupper(substr($name,0,1)) ?></div>
          <div style="line-height:1">
            <div style="font-weight:700;font-size:14px"><?= htmlspecialchars($name) ?></div>
            <div class="muted" style="margin-top:2px">Admin</div>
          </div>
        </div>
      </div>
    </div>
  </header>

  <main>
    <section class="stats" aria-label="Platform statistics">
      <div class="card">
        <h3>Total Tests</h3>
        <div class="value"><?= (int)$total_tests ?></div>
        <div class="sub muted">All tests on the platform</div>
      </div>

      <div class="card">
        <h3>Students</h3>
        <div class="value"><?= (int)$total_students ?></div>
        <div class="sub muted">Active student accounts</div>
      </div>

      <div class="card">
        <h3>Admins</h3>
        <div class="value"><?= (int)$total_admins ?></div>
        <div class="sub muted">Platform administrators</div>
      </div>
    </section>

    <section class="panel" aria-label="Main panel">
      <div>
        <div style="margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
          <h2 style="margin:0">Recent Tests</h2>
          <div class="muted">Latest 6 tests</div>
        </div>

        <div id="testsList" class="table-list" aria-live="polite">
          <?php while($t = $recent_tests->fetch_assoc()): ?>
            <div class="test-row" data-title="<?= htmlspecialchars(strtolower($t['title'])) ?>">
              <div class="test-left">
                <div style="min-width:44px; height:44px; border-radius:10px; background:linear-gradient(135deg,#fff,#fbfdff); display:grid; place-items:center; box-shadow:var(--shadow-sm); font-weight:800; color:var(--primary)"><?= htmlspecialchars(substr($t['title'],0,1)) ?></div>
                <div>
                  <div style="font-weight:800; font-size:15px; color:#071033"><?= htmlspecialchars($t['title']) ?></div>
                  <div class="test-meta" style="margin-top:6px">
                    <span class="muted">Created: </span>
                    <span class="muted nowrap"><?= htmlspecialchars($t['created_at']) ?></span>
                    &nbsp;•&nbsp;
                    <span class="muted">Test ID:</span> <span style="font-weight:700">#<?= (int)$t['id'] ?></span>
                  </div>
                </div>
              </div>

              <div style="display:flex; gap:12px; align-items:center;">
                <div>
                  <?php if ($t['status'] === 'published'): ?>
                    <span class="badge published">Published</span>
                  <?php else: ?>
                    <span class="badge draft">Draft</span>
                  <?php endif; ?>
                </div>

                <div class="actions">
                  <a class="action-link" href="add_question.php?test_db_id=<?= (int)$t['id'] ?>" title="Add Questions">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M12 5v14M5 12h14" stroke="#0b6ef6" stroke-width="1.6" stroke-linecap="round"/></svg>
                    Add Qs
                  </a>
                  <a class="action-link" href="view_test.php?test_id=<?= (int)$t['id'] ?>" title="View Test">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M3 12s4-8 9-8 9 8 9 8-4 8-9 8-9-8-9-8z" stroke="#0b6ef6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    View
                  </a>
                  <a class="action-link" href="view_results.php?test_id=<?= (int)$t['id'] ?>" title="View Results">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M12 2v20M2 12h20" stroke="#0b6ef6" stroke-width="1.6" stroke-linecap="round"/></svg>
                    Results
                  </a>
                </div>
              </div>
            </div>
          <?php endwhile; ?>

          <?php if ($recent_tests->num_rows === 0): ?>
            <div class="empty">No tests yet. Use <strong>Create Test</strong> to get started.</div>
          <?php endif; ?>
        </div>
      </div>

      <aside>
        <div class="side-card card">
          <a href="manage_users.php">Manage Users</a>
          <a href="create_test.php">Create New Test</a>
          <a href="assign_students.php">Assign Students</a>
          <a href="proctoring_logs_view.php">View Proctoring Logs</a>
        </div>

        <div class="card">
          <h3 style="margin:0 0 6px 0">Help & Tips</h3>
          <p class="help muted" style="margin:0">
            • Create tests and add questions before assigning students.<br>
            • Encourage students to use Chrome/Edge for best webcam & proctoring support.<br>
            • Use the Results view to export CSV if needed.
          </p>
        </div>
      </aside>
    </section>
  </main>

  <script>
    // client-side search (simple)
    const search = document.getElementById('tableSearch');
    const list = document.getElementById('testsList');
    search.addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      const rows = list.querySelectorAll('.test-row');
      let visible = 0;
      rows.forEach(r => {
        const title = r.getAttribute('data-title') || '';
        if (q === '' || title.indexOf(q) !== -1) {
          r.style.display = 'flex';
          visible++;
        } else {
          r.style.display = 'none';
        }
      });
      // show empty state if needed
      if (visible === 0) {
        if (!document.getElementById('noResults')) {
          const empty = document.createElement('div');
          empty.id = 'noResults';
          empty.className = 'empty';
          empty.innerHTML = 'No tests match your search.';
          list.appendChild(empty);
        }
      } else {
        const no = document.getElementById('noResults');
        if (no) no.remove();
      }
    });
  </script>
</body>
</html>
