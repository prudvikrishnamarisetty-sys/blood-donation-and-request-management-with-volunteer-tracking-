/* BloodFlow — Dynamic Animation Engine
   Classic Enhanced Dynamic Theme
   ================================================== */

// ── Count-Up Numbers ───────────────────────────────
function countUp(el) {
  const target = parseInt(el.dataset.count || 0, 10);
  if (!target) { el.textContent = '0'; return; }
  const duration = 1600;
  const step = Math.ceil(target / (duration / 16));
  let current = 0;
  const timer = setInterval(() => {
    current = Math.min(current + step, target);
    el.textContent = current.toLocaleString();
    if (current >= target) clearInterval(timer);
  }, 16);
}

// ── Ripple Effect on any .btn ──────────────────────
function addRipple(e) {
  const btn = e.currentTarget;
  const ripple = document.createElement('span');
  const rect = btn.getBoundingClientRect();
  Object.assign(ripple.style, {
    position:'absolute', borderRadius:'50%', background:'rgba(255,255,255,0.25)',
    width:'0', height:'0', left:(e.clientX-rect.left)+'px', top:(e.clientY-rect.top)+'px',
    transform:'translate(-50%,-50%)',
    animation:'rippleBtn 0.55s linear forwards', pointerEvents:'none'
  });
  if (getComputedStyle(btn).position === 'static') btn.style.position = 'relative';
  btn.style.overflow = 'hidden';
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 600);
}

// Inject ripple keyframe once
const rStyle = document.createElement('style');
rStyle.textContent = '@keyframes rippleBtn{to{width:300px;height:300px;opacity:0;}}';
document.head.appendChild(rStyle);

// ── Scroll Reveal (stagger children) ──────────────
function revealOnScroll() {
  const revealTargets = document.querySelectorAll(
    '.kpi-card, .req-card, .task-card, .flow-step, .blood-card, .panel, .user-row, .notif-full-item, .audit-item'
  );
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry, i) => {
      if (entry.isIntersecting) {
        const el = entry.target;
        el.style.animationDelay = (i * 0.05) + 's';
        el.classList.add('revealed');
        io.unobserve(el);
      }
    });
  }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });
  revealTargets.forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(14px)';
    el.style.transition = 'opacity 0.45s ease, transform 0.45s ease';
    io.observe(el);
  });
}

// Apply revealed state
const revealStyle = document.createElement('style');
revealStyle.textContent = '.revealed{opacity:1!important;transform:translateY(0)!important;}';
document.head.appendChild(revealStyle);

// ── Tab System (data-tab attributes) ──────────────
function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      const container = btn.closest('[data-tabs]') || document.body;
      // Deactivate all
      container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      container.querySelectorAll('.tab-pane').forEach(p => {
        p.style.display = 'none';
        p.style.opacity = '0';
      });
      // Activate clicked
      btn.classList.add('active');
      const pane = document.getElementById(target);
      if (pane) {
        pane.style.display = 'block';
        setTimeout(() => { pane.style.transition = 'opacity 0.3s ease'; pane.style.opacity = '1'; }, 10);
      }
    });
  });
  // Init: show first pane in each tab group or keep existing active
  document.querySelectorAll('[data-tabs]').forEach(container => {
    const activeBtn = container.querySelector('.tab-btn.active');
    const firstBtn  = container.querySelector('.tab-btn');
    const btn = activeBtn || firstBtn;
    if (btn) btn.click();
  });
}

// ── Navbar scroll shadow ────────────────────────────
function initNavbar() {
  const nav = document.querySelector('.navbar');
  if (!nav) return;
  window.addEventListener('scroll', () => {
    nav.style.boxShadow = window.scrollY > 10
      ? '0 4px 24px rgba(0,0,0,0.5), 0 1px 0 rgba(212,168,67,0.12)'
      : '';
  }, { passive: true });
}

// ── Tilt effect on KPI cards ───────────────────────
function initTilt() {
  document.querySelectorAll('.kpi-card').forEach(card => {
    card.addEventListener('mousemove', e => {
      const rect = card.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = `translateY(-4px) rotateX(${-y*8}deg) rotateY(${x*8}deg)`;
    });
    card.addEventListener('mouseleave', () => {
      card.style.transform = '';
      card.style.transition = 'transform 0.4s ease';
    });
    card.addEventListener('mouseenter', () => {
      card.style.transition = 'transform 0.1s ease';
    });
  });
}

// ── Gold shimmer on hover (brand accent elements) ──
function initShimmer() {
  document.querySelectorAll('.kpi-num, .bg-units, .hstat-num').forEach(el => {
    el.addEventListener('mouseenter', () => {
      el.style.transition = 'color 0.3s';
      el.style.color = 'var(--gold-l)';
    });
    el.addEventListener('mouseleave', () => {
      el.style.color = '';
    });
  });
}

// ── Animated progress bars ─────────────────────────
function animateProgressBars() {
  document.querySelectorAll('.bg-bar-fill').forEach(bar => {
    const w = bar.style.width;
    bar.style.width = '0';
    setTimeout(() => {
      bar.style.transition = 'width 0.8s cubic-bezier(0.4,0,0.2,1)';
      bar.style.width = w;
    }, 200);
  });
}

// ── Flash auto-dismiss with fade ───────────────────
function initFlashDismiss() {
  setTimeout(() => {
    document.querySelectorAll('.flash').forEach((f, i) => {
      setTimeout(() => {
        f.style.transition = 'opacity 0.5s, transform 0.5s';
        f.style.opacity = '0';
        f.style.transform = 'translateX(110%)';
        setTimeout(() => f.remove(), 520);
      }, i * 150);
    });
  }, 4500);
}

// ── Boot ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Count-up with IntersectionObserver
  const cObserver = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) { countUp(e.target); cObserver.unobserve(e.target); } });
  }, { threshold: 0.3 });
  document.querySelectorAll('[data-count]').forEach(el => cObserver.observe(el));

  // Ripple on buttons
  document.querySelectorAll('.btn').forEach(btn => btn.addEventListener('click', addRipple));

  // Scroll reveal
  revealOnScroll();

  // Tab system
  initTabs();

  // Navbar
  initNavbar();

  // Tilt
  initTilt();

  // Shimmer on numbers
  initShimmer();

  // Progress bars
  animateProgressBars();

  // Flash
  initFlashDismiss();
});
