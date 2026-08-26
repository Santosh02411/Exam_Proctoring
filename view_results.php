<?php
session_start();
if (!isset($_SESSION['user_id']) || $_SESSION['role'] !== 'admin') { header("Location: login.html"); exit; }
$conn = new mysqli("localhost","root","","exam_proctoring");
if ($conn->connect_error) die("DB err: ".$conn->connect_error);

$test_id = isset($_GET['test_id']) ? (int)$_GET['test_id'] : 0;
$res = $conn->query("SELECT r.*, u.name, u.email FROM test_results r JOIN users u ON u.id=r.student_id WHERE r.test_id=".$test_id." ORDER BY r.submitted_at DESC");
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Results — Test <?= htmlspecialchars($test_id) ?></title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#f6f8fb;
    --card:#ffffff;
    --muted:#6b7280;
    --primary:#0b6ef6;
    --success:#10b981;
    --danger:#ef4444;
    --radius:12px;
    --shadow: 0 12px 30px rgba(11,22,50,0.06);
    font-family: 'Inter', system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
  }
  *{box-sizing:border-box}
  body{ margin:0; background:linear-gradient(180deg,var(--bg),#f3f6fa); color:#071033; padding:22px; -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }

  .container{ max-width:1100px; margin:0 auto; display:grid; gap:16px; }

  header{ display:flex; justify-content:space-between; align-items:center; gap:12px; }
  .back { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px; text-decoration:none; color:var(--muted); background:var(--card); box-shadow:var(--shadow); font-weight:700; }
  h1{ margin:0; font-size:18px; }
  .meta { color:var(--muted); font-size:13px; }

  .controls { display:flex; gap:10px; align-items:center; }
  .search { display:flex; align-items:center; gap:8px; background:var(--card); padding:8px 12px; border-radius:10px; box-shadow:var(--shadow); min-width:220px; }
  .search input{ border:0; outline:0; background:transparent; width:220px; font-size:14px; color:#071033; }

  .btn {
    display:inline-flex; gap:8px; align-items:center; padding:9px 12px; border-radius:10px; text-decoration:none; font-weight:700; font-size:14px; color:#fff; background:var(--primary); box-shadow: 0 8px 20px rgba(11,110,246,0.12);
  }
  .btn.ghost { background:transparent; color:var(--primary); border:1px solid rgba(11,110,246,0.08); box-shadow:none; }

  .card { background:var(--card); border-radius:var(--radius); padding:16px; box-shadow:var(--shadow); }

  .table-wrap { overflow:auto; margin-top:8px; }
  table { width:100%; border-collapse:collapse; min-width:820px; }
  thead th { text-align:left; padding:12px 14px; font-size:13px; color:var(--muted); background:linear-gradient(180deg,#fff,#fbfdff); position:sticky; top:0; z-index:1; }
  tbody td { padding:12px 14px; border-bottom:1px solid #f1f5f9; font-size:14px; color:#071033; vertical-align:middle; }
  tbody tr:hover td { background:#fbfdff; }

  .badge {
    display:inline-block; padding:6px 10px; border-radius:999px; font-size:12px; font-weight:700;
  }
  .badge.passed { background:rgba(16,185,129,0.12); color:var(--success); }
  .badge.failed { background:rgba(239,68,68,0.08); color:var(--danger); }

  .small { font-size:13px; color:var(--muted); }

  @media (max-width:900px) {
    .search input{ width:120px; }
    table { min-width:720px; }
  }
  @media (max-width:640px) {
    header{ flex-direction:column; align-items:flex-start; gap:10px; }
    table { min-width:600px; }
  }

  .empty { padding:28px; text-align:center; color:var(--muted); }
</style>
</head>
<body>
  <div class="container">
    <header>
      <div style="display:flex;gap:12px;align-items:center">
        <a class="back" href="admin_dashboard.php" title="Back to dashboard">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M15 18l-6-6 6-6" stroke="#0b1220" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Back
        </a>
        <div>
          <h1>Results</h1>
          <div class="meta">Test ID: <strong><?= htmlspecialchars($test_id) ?></strong></div>
        </div>
      </div>

      <div class="controls">
        <div class="search" role="search" aria-label="Search results">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden><path d="M21 21l-4.35-4.35" stroke="#94a3b8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#94a3b8" stroke-width="1.6"/></svg>
          <input id="tableSearch" type="search" placeholder="Search students or email...">
        </div>

        <a class="btn ghost" id="exportCsv" href="#" title="Export visible results to CSV">Export CSV</a>
      </div>
    </header>

    <div class="card">
      <div class="small" style="margin-bottom:8px">Showing <strong id="rowCount"><?= $res->num_rows ?></strong> result(s).</div>

      <div class="table-wrap">
        <table id="resultsTable" aria-label="Test results table">
          <thead>
            <tr>
              <th style="width:48px">#</th>
              <th>Student</th>
              <th>Score</th>
              <th>Total</th>
              <th>Passed</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody id="resultsTbody">
            <?php $i=1; while($r=$res->fetch_assoc()): ?>
              <tr data-search="<?= htmlspecialchars(strtolower($r['name'].' '.$r['email'])) ?>">
                <td><?= $i++ ?></td>
                <td>
                  <div style="font-weight:700"><?= htmlspecialchars($r['name']) ?></div>
                  <div class="small"><?= htmlspecialchars($r['email']) ?></div>
                </td>
                <td><?= htmlspecialchars($r['score']) ?></td>
                <td><?= htmlspecialchars($r['total_marks']) ?></td>
                <td>
                  <?php if ($r['passed']): ?>
                    <span class="badge passed">Passed</span>
                  <?php else: ?>
                    <span class="badge failed">Failed</span>
                  <?php endif; ?>
                </td>
                <td><div class="small"><?= htmlspecialchars($r['submitted_at']) ?></div></td>
              </tr>
            <?php endwhile; ?>
          </tbody>
        </table>

        <?php if ($res->num_rows === 0): ?>
          <div class="empty">No results found for this test.</div>
        <?php endif; ?>
      </div>
    </div>
  </div>

  <script>
    // client-side search filter
    const searchInput = document.getElementById('tableSearch');
    const tbody = document.getElementById('resultsTbody');
    const rowCountEl = document.getElementById('rowCount');

    function updateRowCount() {
      const rows = tbody.querySelectorAll('tr');
      let visible = 0;
      rows.forEach(r => { if (r.style.display !== 'none') visible++; });
      rowCountEl.textContent = visible;
    }

    searchInput.addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      const rows = tbody.querySelectorAll('tr');
      rows.forEach(r => {
        const s = r.getAttribute('data-search') || '';
        r.style.display = (q === '' || s.indexOf(q) !== -1) ? '' : 'none';
      });
      updateRowCount();
    });

    // export visible rows to CSV (client-side)
    document.getElementById('exportCsv').addEventListener('click', function (ev) {
      ev.preventDefault();
      const rows = Array.from(tbody.querySelectorAll('tr')).filter(r => r.style.display !== 'none');
      if (rows.length === 0) { alert('No visible rows to export.'); return; }

      const csvRows = [];
      // headers
      csvRows.push(['#','Student Name','Email','Score','Total Marks','Passed','Submitted At'].join(','));

      rows.forEach(r => {
        const cells = r.querySelectorAll('td');
        const idx = cells[0].innerText.trim();
        const name = cells[1].querySelector('div').innerText.replace(/,/g,'');
        const email = cells[1].querySelectorAll('div')[1].innerText.replace(/,/g,'');
        const score = cells[2].innerText.trim();
        const total = cells[3].innerText.trim();
        const passed = cells[4].innerText.trim();
        const submitted = cells[5].innerText.trim();

        csvRows.push([idx, `"${name}"`, `"${email}"`, score, total, `"${passed}"`, `"${submitted}"`].join(','));
      });

      const csvString = csvRows.join('\n');
      const blob = new Blob([csvString], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `test_${<?= json_encode($test_id) ?>}_results.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });
  </script>
</body>
</html>
