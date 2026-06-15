/* DOSSIER PAGE - TECH DEMO SHOWCASE */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

/* HELPERS */
function resizeCanvas(canvas, container) {
  const rect = container.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  const ctx = canvas.getContext('2d');
  if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w: rect.width, h: rect.height, dpr };
}

function lerp(a, b, t) { return a + (b - a) * t; }

/* ===== 1. CYBERBRAIN SPHERE ===== */
function initCyberbrain() {
  const container = document.getElementById('cyberbrainContainer');
  if (!container) return;

  const rect = container.getBoundingClientRect();
  const w = rect.width, h = rect.height;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050505);
  scene.fog = new THREE.Fog(0x050505, 5, 12);

  const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 20);
  camera.position.set(0, 0.5, 5.5);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x050505);
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.8;
  controls.minDistance = 2.5;
  controls.maxDistance = 10;

  /* ---- Lights ---- */
  const ambient = new THREE.AmbientLight(0x222244, 0.6);
  scene.add(ambient);
  const light1 = new THREE.DirectionalLight(0x4488ff, 1.5);
  light1.position.set(3, 4, 2);
  scene.add(light1);
  const light2 = new THREE.DirectionalLight(0xff4488, 0.5);
  light2.position.set(-3, -2, 1);
  scene.add(light2);

  /* ---- Sphere with scanline shader ---- */
  const sphereGeo = new THREE.SphereGeometry(2.0, 64, 48);
  const sphereMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uColor1: { value: new THREE.Color(0x0088ff) },
      uColor2: { value: new THREE.Color(0x0044aa) },
      uScanColor: { value: new THREE.Color(0x00ccff) },
    },
    transparent: true,
    opacity: 0.4,
    side: THREE.DoubleSide,
    depthWrite: false,
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vNormal;
      varying vec3 vPosition;
      void main() {
        vUv = uv;
        vNormal = normalize(normalMatrix * normal);
        vPosition = (modelViewMatrix * vec4(position, 1.0)).xyz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform vec3 uColor1;
      uniform vec3 uColor2;
      uniform vec3 uScanColor;
      varying vec2 vUv;
      varying vec3 vNormal;

      void main() {
        float scanY = mod(vUv.y * 40.0 + uTime * 0.5, 1.0);
        float scanline = smoothstep(0.96, 1.0, scanY);

        float gridX = step(0.5, fract(vUv.x * 24.0));
        float gridY = step(0.5, fract(vUv.y * 16.0));
        float grid = max(gridX, gridY) * 0.15;

        float rim = 1.0 - abs(dot(vNormal, vec3(0.0, 0.0, 1.0)));
        rim = pow(rim, 2.0) * 0.6;

        vec3 color = mix(uColor1, uColor2, vUv.y);
        color += grid;
        color = mix(color, uScanColor, scanline * 0.8);
        color += rim * vec3(0.3, 0.6, 1.0);

        float alpha = 0.25 + rim * 0.5 + scanline * 0.3;
        gl_FragColor = vec4(color, alpha);
      }
    `,
  });
  const sphere = new THREE.Mesh(sphereGeo, sphereMat);
  scene.add(sphere);

  /* ---- Wireframe overlay ---- */
  const wireGeo = new THREE.SphereGeometry(2.01, 24, 18);
  const wireMat = new THREE.MeshBasicMaterial({
    wireframe: true,
    color: 0x0066cc,
    transparent: true,
    opacity: 0.15,
  });
  const wireSphere = new THREE.Mesh(wireGeo, wireMat);
  scene.add(wireSphere);

  /* ---- Brain neural network (particles + connections) ---- */
  const brainGroup = new THREE.Group();

  const brainPoints = [];
  function randomInLobe(cx, cy, cz, rx, ry, rz) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.cbrt(Math.random()) * 0.85 + 0.15;
    return new THREE.Vector3(
      cx + r * Math.sin(phi) * Math.cos(theta) * rx,
      cy + r * Math.cos(phi) * ry,
      cz + r * Math.sin(phi) * Math.sin(theta) * rz
    );
  }
  for (let i = 0; i < 180; i++) {
    brainPoints.push(randomInLobe(-0.5, 0.0, 0, 0.9, 0.7, 0.5));
  }
  for (let i = 0; i < 180; i++) {
    brainPoints.push(randomInLobe(0.5, 0.0, 0, 0.9, 0.7, 0.5));
  }
  for (let i = 0; i < 40; i++) {
    brainPoints.push(randomInLobe(0.0, -0.4, -0.3, 0.5, 0.3, 0.4));
  }

  /* Particle system */
  const particlePositions = new Float32Array(brainPoints.length * 3);
  const particleColors = new Float32Array(brainPoints.length * 3);
  for (let i = 0; i < brainPoints.length; i++) {
    particlePositions[i*3] = brainPoints[i].x;
    particlePositions[i*3+1] = brainPoints[i].y;
    particlePositions[i*3+2] = brainPoints[i].z;
    const brightness = 0.4 + Math.random() * 0.6;
    particleColors[i*3] = 0.2 + Math.random() * 0.3;
    particleColors[i*3+1] = 0.4 + brightness * 0.5;
    particleColors[i*3+2] = 0.8 + brightness * 0.2;
  }

  const particleGeo = new THREE.BufferGeometry();
  particleGeo.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
  particleGeo.setAttribute('color', new THREE.BufferAttribute(particleColors, 3));

  const particleMat = new THREE.PointsMaterial({
    size: 0.06,
    vertexColors: true,
    transparent: true,
    opacity: 0.9,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const particles = new THREE.Points(particleGeo, particleMat);
  brainGroup.add(particles);

  /* Connections between nearby particles */
  const connectionPositions = [];
  for (let i = 0; i < brainPoints.length; i++) {
    for (let j = i + 1; j < brainPoints.length; j++) {
      const dist = brainPoints[i].distanceTo(brainPoints[j]);
      if (dist < 0.4 && Math.random() < 0.15) {
        connectionPositions.push(brainPoints[i].x, brainPoints[i].y, brainPoints[i].z);
        connectionPositions.push(brainPoints[j].x, brainPoints[j].y, brainPoints[j].z);
      }
    }
  }

  const connGeo = new THREE.BufferGeometry();
  connGeo.setAttribute('position', new THREE.Float32BufferAttribute(connectionPositions, 3));
  const connMat = new THREE.LineBasicMaterial({
    color: 0x0066ff,
    transparent: true,
    opacity: 0.15,
  });
  const connections = new THREE.LineSegments(connGeo, connMat);
  brainGroup.add(connections);

  /* Center glow */
  const glowGeo = new THREE.SphereGeometry(0.08, 8, 8);
  const glowMat = new THREE.MeshBasicMaterial({
    color: 0x0088ff,
    transparent: true,
    opacity: 0.6,
  });
  const centerGlow = new THREE.Mesh(glowGeo, glowMat);
  brainGroup.add(centerGlow);

  scene.add(brainGroup);

  /* ---- Stars background ---- */
  const starCount = 800;
  const starGeo2 = new THREE.BufferGeometry();
  const starPos = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount * 3; i++) {
    starPos[i] = (Math.random() - 0.5) * 40;
  }
  starGeo2.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  const starMat2 = new THREE.PointsMaterial({
    color: 0x4488ff,
    size: 0.02,
    transparent: true,
    opacity: 0.4,
    blending: THREE.AdditiveBlending,
  });
  const stars = new THREE.Points(starGeo2, starMat2);
  scene.add(stars);

  /* ---- Resize ---- */
  function resize() {
    const r = container.getBoundingClientRect();
    const nw = r.width, nh = r.height;
    camera.aspect = nw / nh;
    camera.updateProjectionMatrix();
    renderer.setSize(nw, nh);
  }
  window.addEventListener('resize', resize);

  /* ---- Animate ---- */
  function animate(time) {
    requestAnimationFrame(animate);
    const t = time * 0.001;

    sphereMat.uniforms.uTime.value = t;
    sphere.rotation.y = t * 0.05;
    wireSphere.rotation.y = t * 0.05;
    brainGroup.rotation.y = t * 0.03;
    brainGroup.rotation.x = Math.sin(t * 0.015) * 0.1;

    controls.update();
    renderer.render(scene, camera);
  }

  animate(0);
}

/* ===== 2. WAVEFORM GRAPH ===== */
function initWaveform() {
  const container = document.getElementById('waveformContainer');
  const canvas = document.getElementById('waveformCanvas');
  if (!container || !canvas) return;

  let dims = resizeCanvas(canvas, container);
  let scroll = 0;
  let phase = 0;

  function draw() {
    dims = resizeCanvas(canvas, container);
    drawFrame();
  }

  function drawFrame() {
    const ctx = canvas.getContext('2d');
    const { w, h } = dims;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);

    const pad = 20;
    const gw = w - pad * 2;
    const gh = h - pad * 2;
    const cx = pad;
    const cy = pad;

    /* Grid and notches */
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 0.5;
    const notchSpacing = 30;
    for (let x = 0; x <= gw; x += notchSpacing) {
      ctx.beginPath();
      ctx.moveTo(cx + x, cy);
      ctx.lineTo(cx + x, cy + gh);
      ctx.stroke();
    }
    for (let y = 0; y <= gh; y += notchSpacing) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + y);
      ctx.lineTo(cx + gw, cy + y);
      ctx.stroke();
    }

    /* XY notch ticks */
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= gw; x += notchSpacing * 2) {
      ctx.beginPath();
      ctx.moveTo(cx + x, cy + gh);
      ctx.lineTo(cx + x, cy + gh + 5);
      ctx.stroke();
    }
    for (let y = 0; y <= gh; y += notchSpacing * 2) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + y);
      ctx.lineTo(cx - 5, cy + y);
      ctx.stroke();
    }

    /* Waveforms */
    function drawWave(offsetY, phaseOff, color, amplitude) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.shadowColor = color;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      for (let px = 0; px <= gw; px++) {
        const t = (px / gw) * Math.PI * 4 + scroll;
        const yVal = Math.sin(t + phaseOff) * amplitude * 0.5
                   + Math.sin(t * 2.3 + phaseOff * 1.5) * amplitude * 0.3
                   + Math.sin(t * 0.7 + phaseOff * 2) * amplitude * 0.2;
        const py = cy + gh / 2 + offsetY + yVal;
        px === 0 ? ctx.moveTo(cx + px, py) : ctx.lineTo(cx + px, py);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    drawWave(-8, phase, 'rgba(0,170,255,0.8)', 18);
    drawWave(8, phase + 0.8, 'rgba(255,0,170,0.7)', 18);

    /* Center line */
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 0.5;
    ctx.setLineDash([3, 5]);
    ctx.beginPath();
    ctx.moveTo(cx, cy + gh / 2);
    ctx.lineTo(cx + gw, cy + gh / 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function animate() {
    scroll += 0.03;
    phase += 0.01;
    drawFrame();
    requestAnimationFrame(animate);
  }

  window.addEventListener('resize', draw);
  animate();
}

/* ===== 3. NEURAL GRID ===== */
function initNeuralGrid() {
  const canvas = document.getElementById('neuralGridCanvas');
  const densityEl = document.getElementById('synapticDensity');
  const container = document.getElementById('neuralGridContainer');
  if (!canvas || !container) return;

  const cols = 16, rows = 10;
  let cells = [];
  let targets = [];
  let dims;

  function init() {
    dims = resizeCanvas(canvas, container);
    /* Account for the text above the canvas */
    const textEl = document.getElementById('neuralTextTop');
    const textH = textEl ? textEl.offsetHeight + 30 : 40;
    dims = resizeCanvas(canvas, { getBoundingClientRect: () => ({
      width: dims.w,
      height: Math.max(100, dims.h - textH),
    })});

    cells = [];
    targets = [];
    for (let i = 0; i < cols * rows; i++) {
      cells.push(Math.random());
      targets.push(Math.random() < 0.3 ? 1 : 0);
    }
  }

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const rect = container.getBoundingClientRect();
    const textEl = document.getElementById('neuralTextTop');
    const textH = textEl ? textEl.offsetHeight + 30 : 40;
    const cw = rect.width;
    const ch = Math.max(100, rect.height - textH - 10);
    ctx.fillStyle = '#080808';
    ctx.fillRect(0, 0, cw, ch);

    const gap = 4;
    const cellW = (cw - gap * (cols + 1)) / cols;
    const cellH = (ch - gap * (rows + 1)) / rows;

    let lit = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        cells[idx] += (targets[idx] - cells[idx]) * 0.05;
        const val = cells[idx];
        const x = gap + c * (cellW + gap);
        const y = gap + r * (cellH + gap);

        if (val > 0.05) {
          const bright = Math.min(1, val * 1.5);
          ctx.fillStyle = `rgba(0, ${Math.floor(170 * bright)}, ${Math.floor(255 * bright)}, ${bright * 0.8})`;
          ctx.fillRect(x, y, cellW, cellH);
          lit++;
        } else {
          ctx.fillStyle = 'rgba(255,255,255,0.03)';
          ctx.fillRect(x, y, cellW, cellH);
        }
      }
    }

    if (densityEl) {
      densityEl.textContent = ((lit / (cols * rows)) * 100).toFixed(1);
    }
  }

  function update() {
    if (Math.random() < 0.1) {
      for (let i = 0; i < targets.length; i++) {
        if (Math.random() < 0.3) {
          targets[i] = Math.random() < 0.5 ? 0 : 0.5 + Math.random() * 0.5;
        }
      }
    }
  }

  function animate() {
    update();
    draw();
    requestAnimationFrame(animate);
  }

  init();
  animate();
}

/* ===== 4. FOLDING PANEL ===== */
function initFoldingPanel() {
  const bar = document.getElementById('foldBar');
  const box = document.getElementById('foldBox');
  if (!bar || !box) return;

  let folded = false;

  bar.addEventListener('click', function() {
    folded = !folded;
    box.classList.toggle('folded', folded);
    bar.innerHTML = folded ? '&#9660; EXPAND' : '&#9650; COLLAPSE';
  });
}

/* ===== 5. SPINNING GLOBE (inside warning blocks) ===== */
function initGlobe() {
  const canvas = document.getElementById('globeCanvas');
  if (!canvas) return;

  const size = 50;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + 'px';
  canvas.style.height = size + 'px';

  const ctx = canvas.getContext('2d');
  let angle = 0;

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);

    const cx = size / 2, cy = size / 2, r = 20;

    /* Globe circle */
    ctx.strokeStyle = '#0088ff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    /* Latitude lines */
    for (let lat = -60; lat <= 60; lat += 30) {
      const rad = r * Math.cos(lat * Math.PI / 180);
      const yOff = r * Math.sin(lat * Math.PI / 180);
      ctx.beginPath();
      ctx.ellipse(cx, cy + yOff, rad, rad * 0.3, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    /* Longitude lines */
    for (let lon = 0; lon < 360; lon += 30) {
      const a = (lon + angle) * Math.PI / 180;
      ctx.beginPath();
      ctx.ellipse(cx, cy, r * Math.abs(Math.cos(a)), r, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    /* Highlight band */
    ctx.strokeStyle = 'rgba(0,200,255,0.3)';
    ctx.lineWidth = 2;
    const hA = angle * Math.PI / 180;
    ctx.beginPath();
    ctx.ellipse(cx, cy, r * Math.abs(Math.cos(hA)), r, 0, 0, Math.PI * 2);
    ctx.stroke();

    /* Center dot */
    ctx.fillStyle = '#00ccff';
    ctx.beginPath();
    ctx.arc(cx, cy, 2, 0, Math.PI * 2);
    ctx.fill();
  }

  function animate() {
    angle += 0.5;
    if (angle >= 360) angle -= 360;
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== 6. GAUGE GRID ===== */
function initGaugeGrid() {
  const canvas = document.getElementById('gaugeGridCanvas');
  const container = document.getElementById('gaugeGridContainer');
  if (!canvas || !container) return;

  let dims;
  let needlePos = 0.5;
  let needleTarget = 0.5;
  const gridSpacing = 30;

  function resize() {
    dims = resizeCanvas(canvas, container);
  }
  resize();
  window.addEventListener('resize', resize);

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);

    const gaugeW = 20;
    const gridLeft = gaugeW + 15;
    const gridRight = w - 10;

    /* Gauge track on left */
    const gaugeTop = 15;
    const gaugeBot = h - 15;
    const gaugeH = gaugeBot - gaugeTop;

    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.strokeRect(3, gaugeTop, gaugeW - 6, gaugeH);

    /* Gauge tick marks */
    for (let i = 0; i <= 10; i++) {
      const y = gaugeTop + (gaugeH * i) / 10;
      const tickLen = i % 5 === 0 ? 10 : 5;
      ctx.strokeStyle = i % 5 === 0 ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)';
      ctx.beginPath();
      ctx.moveTo(3, y);
      ctx.lineTo(3 + tickLen, y);
      ctx.stroke();
    }

    /* Gauge needle */
    const needleY = gaugeTop + gaugeH * (1 - needlePos);
    ctx.strokeStyle = '#d00';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#d00';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(3, needleY);
    ctx.lineTo(gaugeW + 5, needleY);
    ctx.stroke();
    ctx.shadowBlur = 0;

    /* Needle dot */
    ctx.fillStyle = '#ff2222';
    ctx.beginPath();
    ctx.arc(gaugeW, needleY, 3, 0, Math.PI * 2);
    ctx.fill();

    /* Grid with + intersections */
    const gridW = gridRight - gridLeft;
    const cols = Math.floor(gridW / gridSpacing);
    const rows = Math.floor(gaugeH / gridSpacing);
    const actualSpacingX = gridW / cols;
    const actualSpacingY = gaugeH / rows;

    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 0.5;

    for (let r = 0; r <= rows; r++) {
      ctx.beginPath();
      ctx.moveTo(gridLeft, gaugeTop + r * actualSpacingY);
      ctx.lineTo(gridRight, gaugeTop + r * actualSpacingY);
      ctx.stroke();
    }
    for (let c = 0; c <= cols; c++) {
      ctx.beginPath();
      ctx.moveTo(gridLeft + c * actualSpacingX, gaugeTop);
      ctx.lineTo(gridLeft + c * actualSpacingX, gaugeBot);
      ctx.stroke();
    }

    /* + markers at intersections */
    const crossSize = 3;
    for (let r = 0; r <= rows; r++) {
      for (let c = 0; c <= cols; c++) {
        const px = gridLeft + c * actualSpacingX;
        const py = gaugeTop + r * actualSpacingY;
        const glow = 0.3 + Math.sin(r * 2.3 + c * 1.7 + Date.now() * 0.001) * 0.15;
        ctx.strokeStyle = `rgba(0, ${Math.floor(150 + glow * 100)}, 255, ${0.1 + glow * 0.2})`;
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.moveTo(px - crossSize, py);
        ctx.lineTo(px + crossSize, py);
        ctx.moveTo(px, py - crossSize);
        ctx.lineTo(px, py + crossSize);
        ctx.stroke();
      }
    }
  }

  function animate() {
    if (Math.random() < 0.02) {
      needleTarget = Math.random();
    }
    needlePos += (needleTarget - needlePos) * 0.01;
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== 7. SHUFFLE DECK ===== */
function initShuffleDeck() {
  const cards = [
    document.getElementById('shuffle0'),
    document.getElementById('shuffle1'),
    document.getElementById('shuffle2'),
  ].filter(Boolean);

  if (cards.length < 3) return;

  const offsets = [
    { x: -5, y: -4, rot: -3 },
    { x: 0, y: 0, rot: 0 },
    { x: 5, y: 4, rot: 3 },
  ];

  function applyPositions() {
    cards.forEach((card, i) => {
      const o = offsets[i];
      card.style.transform = `translate(${o.x}px, ${o.y}px) rotate(${o.rot}deg)`;
      card.style.zIndex = 3 - i;
    });
  }

  function shuffle() {
    for (let i = offsets.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [offsets[i], offsets[j]] = [offsets[j], offsets[i]];
    }
    applyPositions();
  }

  applyPositions();
  setInterval(shuffle, 3000);
}

/* ===== 8. DNA TRICKLE ===== */
function initDnaScanner() {
  const canvas = document.getElementById('dnaCanvas');
  const container = document.getElementById('dnaContainer');
  if (!canvas || !container) return;

  const bases = ['A', 'T', 'C', 'G', 'A', 'T', 'C', 'G', 'U'];
  let particles = [];
  let dims;

  function resize() {
    dims = resizeCanvas(canvas, container);
  }
  resize();
  window.addEventListener('resize', resize);

  for (let i = 0; i < 30; i++) {
    addColumn();
  }

  function addColumn() {
    const side = Math.random() < 0.5 ? -1 : 1;
    const midX = dims.w / 2;
    const helixR = 20 + Math.random() * 25;
    const startY = -20 - Math.random() * 100;
    particles.push({
      x: midX + side * helixR,
      targetX: midX - side * helixR,
      y: startY,
      speed: 0.3 + Math.random() * 0.6,
      base: bases[Math.floor(Math.random() * bases.length)],
      alpha: 0.2 + Math.random() * 0.8,
      phase: Math.random() * Math.PI * 2,
    });
  }

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;
    ctx.fillStyle = '#080808';
    ctx.fillRect(0, 0, w, h);

    const midX = w / 2;

    /* Notch border effect */
    ctx.strokeStyle = 'rgba(0,100,200,0.15)';
    ctx.lineWidth = 1;
    const notch = 20;
    ctx.beginPath();
    ctx.moveTo(notch, 0);
    ctx.lineTo(w - notch, 0);
    ctx.lineTo(w, notch);
    ctx.lineTo(w, h - notch);
    ctx.lineTo(w - notch, h);
    ctx.lineTo(notch, h);
    ctx.lineTo(0, h - notch);
    ctx.lineTo(0, notch);
    ctx.closePath();
    ctx.stroke();

    /* Double helix guide lines */
    ctx.strokeStyle = 'rgba(0,50,100,0.08)';
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    const guideCount = 120;
    for (let i = 0; i <= guideCount; i++) {
      const t = i / guideCount * Math.PI * 4;
      const y = i / guideCount * h;
      const x1 = midX + Math.sin(t) * 30;
      const x2 = midX + Math.sin(t + Math.PI) * 30;
      if (i === 0) {
        ctx.moveTo(x1, y);
        ctx.lineTo(x2, y);
      } else {
        ctx.lineTo(x1, y);
        ctx.moveTo(x2, y);
        ctx.lineTo(x1, y);
      }
    }
    ctx.stroke();

    /* Background trickle chars */
    ctx.font = '8px monospace';
    ctx.textAlign = 'center';
    for (let y = 0; y < h; y += 6) {
      const x = midX + Math.sin(y * 0.1 + Date.now() * 0.0005) * 28;
      ctx.fillStyle = `rgba(0,50,80,0.08)`;
      ctx.fillText(bases[Math.floor(Math.random() * bases.length)], x, y);
    }

    /* Falling particles */
    particles.forEach(p => {
      p.y += p.speed;
      p.x += (p.targetX - p.x) * 0.01;
      if (p.y > h + 10) {
        p.y = -10;
        p.base = bases[Math.floor(Math.random() * bases.length)];
        const side = Math.random() < 0.5 ? -1 : 1;
        p.x = midX + side * (15 + Math.random() * 35);
        p.targetX = midX - side * (15 + Math.random() * 35);
      }

      const glow = Math.sin(p.y * 0.05 + p.phase) * 0.3 + 0.7;
      ctx.fillStyle = `rgba(0, ${Math.floor(180 + glow * 75)}, ${Math.floor(200 + glow * 55)}, ${p.alpha * glow})`;
      ctx.font = `${9 + glow * 3}px monospace`;
      ctx.textAlign = 'center';
      ctx.fillText(p.base, p.x, p.y);

      /* Connecting line to partner position */
      const partnerX = midX + (midX - p.x);
      ctx.strokeStyle = `rgba(0,100,200,${p.alpha * glow * 0.15})`;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(partnerX, p.y + 2);
      ctx.stroke();
    });

    if (particles.length < 60 && Math.random() < 0.1) {
      addColumn();
    }
  }

  function animate() {
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== 9. DATA STREAM ===== */
function initDataStream() {
  const canvas = document.getElementById('dataStreamCanvas');
  const container = document.getElementById('dataStreamContainer');
  if (!canvas || !container) return;

  const chars = '0123456789ABCDEF';
  const words = ['ACCESS', 'DENIED', 'CLASSIFIED', 'TRACE', 'SIGNAL', 'BREACH',
    'GHOST', 'SYSTEM', 'ALERT', 'SCAN', 'CIPHER', 'DECRYPT', 'WARNING', 'TARGET'];
  let columns = [];
  let dims;
  let fontSize = 10;
  let colW;

  function resize() {
    dims = resizeCanvas(canvas, container);
    fontSize = Math.max(8, Math.min(12, dims.w / 40));
    colW = fontSize * 1.2;
    const numCols = Math.floor(dims.w / colW);
    while (columns.length < numCols) {
      columns.push({
        y: Math.random() * -dims.h,
        speed: 0.5 + Math.random() * 2,
        delay: Math.random() * 100,
        trail: 10 + Math.floor(Math.random() * 20),
        word: words[Math.floor(Math.random() * words.length)],
        wordTimer: 0,
      });
    }
    columns.length = numCols;
  }
  resize();
  window.addEventListener('resize', resize);

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;

    /* Fade trail */
    ctx.fillStyle = 'rgba(8,8,8,0.08)';
    ctx.fillRect(0, 0, w, h);

    /* Left decorative border */
    ctx.strokeStyle = 'rgba(0,200,100,0.08)';
    ctx.lineWidth = 1;
    ctx.strokeRect(1, 1, w - 2, h - 2);

    ctx.font = `${fontSize}px monospace`;
    ctx.textAlign = 'center';

    columns.forEach((col, ci) => {
      col.delay -= 1;
      if (col.delay > 0) return;

      col.y += col.speed;
      col.wordTimer -= 1;

      if (col.y > h + 20) {
        col.y = -20;
        col.speed = 0.5 + Math.random() * 2;
        col.word = words[Math.floor(Math.random() * words.length)];
        col.wordTimer = 30 + Math.floor(Math.random() * 60);
        col.delay = Math.random() * 30;
      }

      const x = ci * colW + colW / 2;

      /* Trail */
      for (let i = 0; i < col.trail; i++) {
        const ty = col.y - i * fontSize;
        if (ty < -fontSize || ty > h + fontSize) continue;
        const alpha = 1 - (i / col.trail);
        const fade = alpha * alpha;
        if (i === 0) {
          ctx.fillStyle = `rgba(200,255,200,${fade})`;
          ctx.shadowColor = 'rgba(0,255,100,0.5)';
          ctx.shadowBlur = 6;
        } else {
          ctx.fillStyle = `rgba(0,${Math.floor(180 * fade)},${Math.floor(100 * fade)},${fade * 0.6})`;
          ctx.shadowBlur = 0;
        }

        let ch;
        if (i === 0 && col.wordTimer > 0 && i < col.word.length) {
          ch = col.word[i];
        } else {
          ch = chars[Math.floor(Math.random() * chars.length)];
        }
        ctx.fillText(ch, x, ty);
      }
      ctx.shadowBlur = 0;
    });
  }

  function animate() {
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== TAB SWITCHING ===== */
function initTabs() {
  document.querySelectorAll('.dossier-tab').forEach(tab => {
    tab.addEventListener('click', function() {
      const tabId = this.dataset.tab;
      document.querySelectorAll('.dossier-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.dossier-pane').forEach(p => p.classList.remove('active'));
      this.classList.add('active');
      const pane = document.getElementById('pane-' + tabId);
      if (pane) pane.classList.add('active');
    });
  });
}

/* ===== INIT ===== */
document.addEventListener('DOMContentLoaded', function() {
  initTabs();
  initCyberbrain();
  initWaveform();
  initNeuralGrid();
  initFoldingPanel();
  initGlobe();
  initGaugeGrid();
  initShuffleDeck();
  initDnaScanner();
  initDataStream();
});
