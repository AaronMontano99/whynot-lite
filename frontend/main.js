import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const GREEN = 0x55ff55, RED = 0xff5555, GOLD = 0xffaa00, BLUE = 0x5555ff, CYAN = 0x55ffff;
const SKY = new THREE.Color(0x8ecbff), SKY_LOCKED = new THREE.Color(0x6b7f99), NIGHT = new THREE.Color(0x0b1026);
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const $ = (id) => document.getElementById(id);
const money = (v, sign = true) =>
  (sign && v >= 0 ? '+' : v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cls = (v) => (v >= 0 ? 'up' : 'down');
const usd = (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// ---------------------------------------------------------------- blocks
// Textures: Pixel Perfection CE (CC BY-SA 4.0). Font: Monocraft (OFL). See vendor/ATTRIBUTION.md.

const BLOCKS = ['grass_block_top', 'grass_block_side', 'dirt', 'stone', 'stone_bricks', 'cobblestone', 'oak_planks',
  'oak_log', 'oak_log_top', 'oak_leaves', 'glass', 'gold_block', 'diamond_block', 'emerald_block', 'redstone_block',
  'iron_block', 'obsidian', 'glowstone', 'bookshelf', 'crafting_table_top', 'crafting_table_side', 'bricks',
  'quartz_block_side', 'spruce_planks', 'dark_oak_planks', 'redstone_lamp_on', 'gravel', 'smooth_stone', 'lapis_block',
  'coal_block', 'sea_lantern', 'beacon', 'white_wool', 'light_blue_wool', 'mossy_stone_bricks', 'polished_andesite',
  'andesite', 'sand', 'chiseled_stone_bricks', 'red_wool', 'yellow_wool', 'lime_wool'];
const images = {};
await Promise.all(BLOCKS.map((n) => new Promise((done) => {
  const img = new Image();
  img.onload = () => { images[n] = img; done(); };
  img.onerror = () => done();                       // a missing texture falls back to a flat colour
  img.src = `vendor/pixelperfection/${n}.png`;
})));
await document.fonts.load('16px Monocraft').catch(() => {});

const B = 2;                                        // world units per block
const GRASS = '#7fc24f', LEAVES = '#4f9a32';
const tiles = {};
function tile(name, tint) {                         // one 16x16 block face, optionally tinted
  const key = name + (tint || '');
  if (tiles[key]) return tiles[key];
  const c = document.createElement('canvas');
  c.width = c.height = 16;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.fillStyle = tint || '#8a8a8a';
  g.fillRect(0, 0, 16, 16);
  if (images[name]) {
    if (tint) g.globalCompositeOperation = 'multiply';
    g.drawImage(images[name], 0, 0, 16, 16, 0, 0, 16, 16);
    g.globalCompositeOperation = 'source-over';
  }
  return (tiles[key] = c);
}
function texture(source, rx = 1, ry = 1) {
  const t = new THREE.CanvasTexture(source);
  t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(rx, ry);
  return t;
}
const lambert = (source, rx, ry) => new THREE.MeshLambertMaterial({ map: texture(source, rx, ry) });

// A box whose faces repeat one texture per block. side/top are texture names or canvases.
function box(w, h, d, side, top = side, tint) {
  const src = (n) => (typeof n === 'string' ? tile(n, tint && (n === 'grass_block_top' || n === 'oak_leaves') ? tint : undefined) : n);
  const own = (n) => typeof n !== 'string';         // a ready-made canvas covers the whole face
  const s = src(side), t = src(top);
  const mats = [
    lambert(s, own(side) ? 1 : d / B, own(side) ? 1 : h / B), lambert(s, own(side) ? 1 : d / B, own(side) ? 1 : h / B),
    lambert(t, own(top) ? 1 : w / B, own(top) ? 1 : d / B), lambert(t, own(top) ? 1 : w / B, own(top) ? 1 : d / B),
    lambert(s, own(side) ? 1 : w / B, own(side) ? 1 : h / B), lambert(s, own(side) ? 1 : w / B, own(side) ? 1 : h / B)];
  return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mats);
}
function put(parent, mesh, x, y, z) { mesh.position.set(x, y, z); parent.add(mesh); return mesh; }

// A wall of blocks with glass and lit windows set into it.
function wall(name, cols, rows, lit, chance = 0.35, dark = 'glass') {
  const c = document.createElement('canvas');
  c.width = cols * 16; c.height = rows * 16;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  for (let r = 0; r < rows; r++) {
    for (let col = 0; col < cols; col++) {
      const window = r % 3 === 1 && col % 2 === 1 && r < rows - 1;
      if (window) { g.fillStyle = '#22384d'; g.fillRect(col * 16, r * 16, 16, 16); }
      g.drawImage(tile(window ? (Math.random() < chance ? lit : dark) : name), col * 16, r * 16);
    }
  }
  return c;
}

function tree(parent, x, z, height = 6) {
  put(parent, box(B, height, B, 'oak_log', 'oak_log_top'), x, height / 2, z);
  put(parent, box(8, 4, 8, 'oak_leaves', 'oak_leaves', LEAVES), x, height + 1, z);
  put(parent, box(4, 2, 4, 'oak_leaves', 'oak_leaves', LEAVES), x, height + 4, z);
}

function glow(color, opacity = 1) {
  return new THREE.MeshBasicMaterial({ color, transparent: true, opacity,
    blending: THREE.AdditiveBlending, depthWrite: false });
}

const unlit = (color) => new THREE.MeshBasicMaterial({ color });

// A block face drawn by hand, for the themed towers. box() and wall() find it by name.
function paint(name, draw) {
  const c = document.createElement('canvas');
  c.width = c.height = 16;
  draw(c.getContext('2d'));
  tiles[name] = c;
}
const fill = (g, color, x = 0, y = 0, w = 16, h = 16) => { g.fillStyle = color; g.fillRect(x, y, w, h); };
paint('kip_panel', (g) => {
  fill(g, '#1b2433'); fill(g, '#2c3a52', 0, 0, 16, 1); fill(g, '#2c3a52', 0, 0, 1, 16);
  fill(g, '#0e141f', 0, 15, 16, 1); fill(g, '#0e141f', 15, 0, 1, 16); fill(g, '#232f44', 2, 8, 12, 1);
  for (const [x, y] of [[2, 2], [13, 2], [2, 13], [13, 13]]) fill(g, '#4a5d80', x, y, 1, 1);
});
paint('kip_window', (g) => { fill(g, '#0b1a2a'); fill(g, '#39e6ff', 2, 5, 12, 6); fill(g, '#c9fbff', 2, 5, 12, 1); });
paint('kip_window_off', (g) => { fill(g, '#0b1a2a'); fill(g, '#12445c', 2, 5, 12, 6); });
paint('kip_pad', (g) => { fill(g, '#141a26'); fill(g, '#1f6f86', 0, 0, 16, 1); fill(g, '#1f6f86', 0, 0, 1, 16); });
paint('chud_black', (g) => { fill(g, '#050805'); for (const [x, y] of [[3, 4], [11, 2], [7, 12], [13, 9]]) fill(g, '#0d3a16', x, y, 1, 1); });
paint('chud_pad', (g) => { fill(g, '#040604'); fill(g, '#0c5a1c', 0, 0, 16, 1); fill(g, '#0c5a1c', 0, 0, 1, 16); fill(g, '#35ff5a', 0, 0, 1, 1); });
paint('chud_neon', (g) => { fill(g, '#35ff5a'); fill(g, '#b8ffc8', 0, 7, 16, 2); });
paint('era_strap', (g) => {
  fill(g, '#5a3420'); fill(g, '#6b3f27', 3, 0, 10, 16); fill(g, '#4a2a19', 0, 15, 16, 1);
  for (let y = 1; y < 16; y += 4) { fill(g, '#d9b98a', 1, y, 1, 2); fill(g, '#d9b98a', 14, y, 1, 2); }
});
paint('era_steel', (g) => { fill(g, '#b9bec6'); for (let y = 1; y < 16; y += 3) fill(g, '#d6dae0', 0, y, 16, 1); fill(g, '#8d939c', 0, 15, 16, 1); });
tiles.prismarine = tile('stone_bricks', '#9ffff0');
tiles.dark_prismarine = tile('stone_bricks', '#4fc4b8');
tiles.kelp = tile('oak_leaves', '#2f9a6a');

// A ring of cubes around the y axis.
function ring(radius, count, size, material) {
  const g = new THREE.Group(), geo = new THREE.BoxGeometry(size, size, size);
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2;
    put(g, new THREE.Mesh(geo, material), Math.cos(a) * radius, 0, Math.sin(a) * radius).rotation.y = -a;
  }
  return g;
}

// ---------------------------------------------------------------- scene

const canvas = $('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = SKY.clone();
scene.fog = new THREE.Fog(SKY.clone(), 150, 420);
const ambient = new THREE.AmbientLight(0xffffff, 1.5);
scene.add(ambient);
const sunLight = new THREE.DirectionalLight(0xfff4d6, 1.7);
sunLight.position.set(70, 120, 50);
scene.add(sunLight);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 700);
camera.position.set(0, 58, 142);
const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 20, -6);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.35;
controls.maxPolarAngle = Math.PI * 0.48;
controls.minDistance = 25;
controls.maxDistance = 230;

const composer = new EffectComposer(renderer);
const renderPass = new RenderPass(scene, camera);
composer.addPass(renderPass);
composer.addPass(new OutputPass());

// the overworld: grass, a plaza, roads, trees, hills, clouds and a square sun
{
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(900, 900), lambert(tile('grass_block_top', GRASS), 450, 450));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  put(scene, box(36, 0.4, 36, 'stone_bricks'), 0, 0.2, 26);              // vault plaza
  put(scene, box(156, 0.3, 6, 'gravel'), 0, 0.15, 4);                    // the road past the towers
  put(scene, box(6, 0.3, 20, 'gravel'), 0, 0.15, 14);
  const rnd = (a, b) => a + Math.random() * (b - a);
  for (let i = 0; i < 46; i++) {
    const a = rnd(0, Math.PI * 2), r = rnd(62, 135);
    const x = Math.round(Math.cos(a) * r / B) * B, z = Math.round(Math.sin(a) * r / B) * B;
    if (Math.abs(x) < 84 && z > -50 && z < 50) continue;                 // keep the town clear
    tree(scene, x, z, 6 + 2 * Math.floor(rnd(0, 3)));
  }
  for (let i = 0; i < 34; i++) {                                         // stepped hills on the horizon
    const a = (i / 34) * Math.PI * 2, r = rnd(165, 235);
    const hill = new THREE.Group();
    let w = B * Math.round(rnd(14, 26)), y = 0;
    for (let step = 0, steps = 2 + Math.floor(rnd(0, 4)); step < steps && w > 6; step++) {
      const h = B * Math.round(rnd(2, 5));
      put(hill, box(w, h, w, step ? 'stone' : 'grass_block_side', step === steps - 1 || w <= 14 ? 'grass_block_top' : 'stone', GRASS), 0, y + h / 2, 0);
      y += h; w -= B * Math.round(rnd(2, 5));
    }
    hill.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
    scene.add(hill);
  }
}
const clouds = [];
for (let i = 0; i < 16; i++) {
  const cloud = new THREE.Mesh(new THREE.BoxGeometry(16 + Math.random() * 30, 3, 10 + Math.random() * 16),
    new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.88, fog: false }));
  cloud.position.set(-260 + Math.random() * 520, 78 + Math.random() * 22, -260 + Math.random() * 520);
  scene.add(cloud);
  clouds.push(cloud);
}
{
  const sun = new THREE.Mesh(new THREE.BoxGeometry(26, 26, 2), new THREE.MeshBasicMaterial({ color: 0xfff3a0, fog: false }));
  sun.position.set(150, 190, -330);
  sun.lookAt(0, 0, 0);
  scene.add(sun);
}

// the vault: a block of gold on a stone plinth
const vault = new THREE.Group();
{
  put(vault, box(16, 2, 16, 'chiseled_stone_bricks' in images ? 'chiseled_stone_bricks' : 'stone_bricks', 'smooth_stone'), 0, 1, 0);
  const gold = put(vault, box(10, 8, 10, 'gold_block'), 0, 6, 0);
  for (const [x, z] of [[-7, -7], [7, -7], [-7, 7], [7, 7]]) put(vault, box(B, B, B, 'emerald_block'), x, 3, z);
  put(vault, box(4, B, 4, 'glowstone'), 0, 11, 0);
  vault.userData.dome = gold;
  vault.position.set(0, 0.4, 26);
  scene.add(vault);
}

// ---------------------------------------------------------------- towers

const towers = new Map();   // agent -> tower
const labels = $('labels');
// The five agents. The trading engine reports their live figures; until it is running the
// city shows this roster on standby.
const ROSTER = [
  { id: 'Kip', name: 'AGENT KIP', symbol: 'QQQ', theme: 'Futuristic', focus: 'Momentum and trend following', room: [0x0b1220, 0x39e6ff] },
  { id: 'Nemo', name: 'AGENT NEMO', symbol: 'SPY', theme: 'Ocean', focus: 'Mean reversion', room: [0x06222b, 0x4fd8ff] },
  { id: 'Chud', name: 'AGENT CHUD', symbol: 'IWM', theme: 'Matrix', focus: 'Volatility and regime transitions', room: [0x030a04, 0x35ff5a] },
  { id: 'Era', name: 'AGENT ERA', symbol: 'QQQ', theme: 'Wrist watch', focus: 'Intraday momentum and opening range', room: [0x1b1410, 0xffd9a0] },
  { id: 'Aura', name: 'AGENT AURA', symbol: 'QQQ', theme: 'Glowing, godly', focus: 'Adaptive quantitative strategy research', room: [0x2a2414, 0xfff0b0] },
];

// Each builder fills the group and returns { y: roof height, tick(time, dt) }.
const BUILDERS = {
  // Futuristic: steel sections hovering on light, neon edges, two orbiting rings.
  Kip(group) {
    const neon = unlit(0x39e6ff), pink = unlit(0xff4fd8);
    put(group, box(24, 1, 24, 'kip_pad'), 0, 0.5, 0);
    put(group, box(14, 2, 14, 'kip_panel'), 0, 2, 0);
    let y = 3;
    const floaters = [];
    [[10, 12], [8, 12], [6, 10]].forEach(([w, h], i) => {
      put(group, new THREE.Mesh(new THREE.BoxGeometry(w - 4, 3, w - 4), neon), 0, y + 1, 0);   // the light it floats on
      y += 2;
      const seg = new THREE.Group();
      put(seg, box(w, h, w, wall('kip_panel', w / B, h / B, 'kip_window', 0.7, 'kip_window_off'), 'kip_panel'), 0, h / 2, 0);
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        put(seg, new THREE.Mesh(new THREE.BoxGeometry(0.4, h, 0.4), i % 2 ? pink : neon), sx * (w / 2 + 0.1), h / 2, sz * (w / 2 + 0.1));
      }
      seg.position.y = y;
      group.add(seg);
      floaters.push({ seg, y, phase: i * 1.3 });
      y += h;
    });
    const low = ring(9, 16, 1, neon), high = ring(7, 12, 1, pink);
    low.position.y = 16; low.rotation.x = 0.18;
    high.position.y = 31; high.rotation.x = -0.25;
    group.add(low, high);
    const drones = [];
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {                    // pylons with hovering drones
      put(group, box(2, 6, 2, 'kip_panel'), sx * 9, 4, sz * 9);
      put(group, new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.5, 2.2), sx * sz > 0 ? neon : pink), sx * 9, 7.2, sz * 9);
      drones.push(put(group, box(1.2, 1.2, 1.2, 'iron_block'), sx * 9, 10, sz * 9));
    }
    return { y: y + 0.3, tick(time, dt) {
      for (const f of floaters) f.seg.position.y = f.y + Math.sin(time * 1.2 + f.phase) * 0.25;
      low.rotation.y += dt * 0.8; high.rotation.y -= dt * 1.1;
      neon.color.setHex(0x39e6ff).multiplyScalar(0.8 + 0.2 * Math.sin(time * 3));
      drones.forEach((d, i) => { d.position.y = 10 + Math.sin(time * 2 + i * 1.6) * 0.8; d.rotation.y = time * 1.5; });
    } };
  },

  // Ocean: a prismarine lighthouse standing in a pool, with an aquarium floor, fish, kelp and bubbles.
  Nemo(group) {
    const water = new THREE.MeshLambertMaterial({ color: 0x2f8fff, transparent: true, opacity: 0.55 });
    const glassy = new THREE.MeshLambertMaterial({ color: 0x55c8ff, transparent: true, opacity: 0.35 });
    put(group, box(24, 1, 24, 'sand'), 0, 0.5, 0);
    const pool = put(group, new THREE.Mesh(new THREE.BoxGeometry(20, 1.2, 20), water), 0, 1.6, 0);
    let y = 1;
    for (const [w, h] of [[10, 12], [8, 10]]) {
      put(group, box(w, h, w, wall('prismarine', w / B, h / B, 'sea_lantern', 0.6), 'dark_prismarine'), 0, y + h / 2, 0);
      put(group, box(w + B, 1, w + B, 'dark_prismarine'), 0, y + h - 0.5, 0);
      y += h;
    }
    const tankY = y + 4;                                                       // the aquarium floor
    put(group, box(2, 8, 2, 'sea_lantern'), 0, tankY, 0);
    put(group, new THREE.Mesh(new THREE.BoxGeometry(8, 8, 8), glassy), 0, tankY, 0);
    y += 8;
    put(group, box(10, 1, 10, 'dark_prismarine'), 0, y + 0.5, 0);
    put(group, box(6, 6, 6, wall('prismarine', 3, 3, 'sea_lantern', 0.9), 'dark_prismarine'), 0, y + 3, 0);
    put(group, box(8, 1, 8, 'light_blue_wool'), 0, y + 5.5, 0);
    y += 6;
    const swimmers = [];
    const school = (color, r, h, speed, a) => { const f = fish(color); group.add(f); swimmers.push({ f, r, h, speed, a }); };
    [0xff8a2a, 0xffd23f, 0xff6fa8].forEach((c, i) => school(c, 2.7, tankY - 2 + i * 2, 1.4, i * 2.1));
    [[0xff8a2a, 9, 9, 0.5], [0xffd23f, 10.5, 16, -0.4], [0xff6fa8, 8.5, 23, 0.6], [0xffffff, 10, 30, -0.5], [0xff8a2a, 7.5, 35, 0.45]]
      .forEach(([c, r, h, speed], i) => school(c, r, h, speed, i * 1.9));
    const kelp = [];
    for (const [dx, dz, h] of [[-9, 9, 7], [-7, 10, 5], [9, 9, 6], [10, -8, 8], [-10, -8, 6]]) {
      const stalk = new THREE.Group();
      put(stalk, box(1, h, 1, 'kelp'), 0, h / 2, 0);
      stalk.position.set(dx, 1, dz);
      group.add(stalk);
      kelp.push(stalk);
    }
    for (const [dx, dz, name, size] of [[9, 6, 'red_wool', 2], [7, 9, 'yellow_wool', 1.4], [-9, 6, 'red_wool', 1.6], [-6, 10, 'light_blue_wool', 1.2],
      [8, -10, 'yellow_wool', 2], [-8, -10, 'red_wool', 2]]) put(group, box(size, size * 1.5, size, name), dx, 1 + size * 0.75, dz);   // coral
    const bubbleMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 });
    const bubbles = Array.from({ length: 14 }, () => {
      const m = put(group, new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), bubbleMat), 0, 0, 0);
      const side = Math.random() < 0.5, far = (Math.random() < 0.5 ? -1 : 1) * (6 + Math.random() * 3), near = -8 + Math.random() * 16;
      m.position.set(side ? far : near, 2 + Math.random() * 38, side ? near : far);
      return { m, speed: 2 + Math.random() * 3 };
    });
    return { y, tick(time, dt) {
      pool.position.y = 1.6 + Math.sin(time * 1.5) * 0.08;
      for (const s of swimmers) {
        s.a += dt * s.speed;
        s.f.position.set(Math.cos(s.a) * s.r, s.h + Math.sin(s.a * 2) * 0.5, Math.sin(s.a) * s.r);
        s.f.rotation.y = -s.a + (s.speed > 0 ? -1 : 1) * Math.PI / 2;      // nose first
      }
      kelp.forEach((k, i) => { k.rotation.z = Math.sin(time * 1.1 + i) * 0.14; });
      for (const b of bubbles) { b.m.position.y += dt * b.speed; if (b.m.position.y > 42) b.m.position.y = 2; }
    } };
  },

  // Matrix: a black monolith with green code raining down every face.
  Chud(group) {
    const W = 10, H = 34, CELL = 8, cols = W / B * 2, rows = H / B * 2;
    const c = document.createElement('canvas');
    c.width = cols * CELL; c.height = rows * CELL;
    const g = c.getContext('2d');
    fill(g, '#000600', 0, 0, c.width, c.height);
    const drops = Array.from({ length: cols }, () => Math.floor(Math.random() * rows * 1.5));
    const glyph = (col, row, color) => {                                       // a random 6x6 character
      fill(g, '#000600', col * CELL, row * CELL, CELL, CELL);
      for (let px = 1; px < 7; px++) for (let py = 1; py < 7; py++) if (Math.random() < 0.45) fill(g, color, col * CELL + px, row * CELL + py, 1, 1);
    };
    const step = () => {
      fill(g, 'rgba(0, 6, 0, 0.07)', 0, 0, c.width, c.height);                 // older characters fade
      drops.forEach((row, col) => {
        if (row - 1 >= 0 && row - 1 < rows) glyph(col, row - 1, '#35ff5a');
        if (row < rows) glyph(col, row, '#d8ffe0');                            // the bright head
        drops[col] = row > rows && Math.random() > 0.7 ? 0 : row + 1;
      });
    };
    for (let i = 0; i < 80; i++) step();
    const rain = texture(c);
    const side = new THREE.MeshBasicMaterial({ map: rain }), cap = lambert(tile('chud_black'));
    put(group, box(24, 1, 24, 'chud_pad'), 0, 0.5, 0);
    put(group, box(12, 1, 12, 'chud_neon'), 0, 1.5, 0);
    put(group, new THREE.Mesh(new THREE.BoxGeometry(W, H, W), [side, side, cap, cap, side, side]), 0, 2 + H / 2, 0);
    let y = 2 + H;
    put(group, box(12, 1, 12, 'chud_neon'), 0, y + 0.5, 0);
    put(group, box(6, 3, 6, 'chud_black'), 0, y + 2.5, 0);
    y += 4;
    const green = unlit(0x35ff5a);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {                    // obelisks
      put(group, box(2, 10, 2, 'obsidian'), sx * 9, 6, sz * 9);
      put(group, new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.2, 1.2), green), sx * 9, 12, sz * 9);
    }
    const bits = Array.from({ length: 16 }, () => {                           // loose code falling around it
      const m = put(group, new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.6, 0.6), glow(0x35ff5a, 0.8)), 0, 0, 0);
      const side = Math.random() < 0.5, far = (Math.random() < 0.5 ? -1 : 1) * (6.5 + Math.random() * 3.5), near = -10 + Math.random() * 20;
      m.position.set(side ? far : near, 2 + Math.random() * 38, side ? near : far);
      return { m, speed: 5 + Math.random() * 7 };
    });
    let wait = 0;
    return { y, tick(time, dt) {
      wait += dt;
      if (wait > 0.07) { wait = 0; step(); rain.needsUpdate = true; }
      for (const b of bits) { b.m.position.y -= dt * b.speed; if (b.m.position.y < 1.5) b.m.position.y = 40; }
      green.color.setHex(0x35ff5a).multiplyScalar(0.7 + 0.3 * Math.sin(time * 4));
    } };
  },

  // Wrist watch: a leather strap standing on its buckle, a gold case, and a dial that keeps New York time.
  Era(group) {
    put(group, box(24, 1, 24, 'polished_andesite'), 0, 0.5, 0);
    put(group, box(12, 2, 12, 'era_steel'), 0, 2, 0);                          // the buckle
    put(group, box(8, 18, 8, wall('era_strap', 4, 9, 'glowstone', 0.4), 'era_strap'), 0, 12, 0);
    put(group, box(10, 1, 10, 'gold_block'), 0, 20.5, 0);
    const cy = 29;                                                             // centre of the case
    put(group, box(16, 16, 6, 'gold_block'), 0, cy, 0);
    put(group, box(2, 3, 3, 'gold_block'), 9, cy, 0);                          // the crown
    put(group, box(1.2, 2, 2, 'era_steel'), 10.4, cy, 0);
    put(group, box(6, 2, 6, 'era_strap'), 0, 38, 0);
    const dial = (() => {
      const c = document.createElement('canvas');
      c.width = c.height = 128;
      const g = c.getContext('2d');
      fill(g, '#17181c', 0, 0, 128, 128);
      g.fillStyle = '#f3ead3'; g.beginPath(); g.arc(64, 64, 58, 0, Math.PI * 2); g.fill();
      g.strokeStyle = '#caa64a'; g.lineWidth = 4; g.stroke();
      g.strokeStyle = '#222222';
      for (let i = 0; i < 60; i++) {
        const a = (i / 60) * Math.PI * 2, r = i % 5 ? 52 : 46;
        g.lineWidth = i % 5 ? 1 : 3;
        g.beginPath(); g.moveTo(64 + Math.sin(a) * r, 64 - Math.cos(a) * r); g.lineTo(64 + Math.sin(a) * 55, 64 - Math.cos(a) * 55); g.stroke();
      }
      g.fillStyle = '#222222'; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.font = '14px Monocraft, ui-monospace, monospace';
      for (const [t, x, y] of [['12', 64, 27], ['3', 103, 65], ['6', 64, 103], ['9', 25, 65]]) g.fillText(t, x, y);
      g.font = '9px Monocraft, ui-monospace, monospace'; g.fillText('ERA', 64, 46);
      return new THREE.MeshBasicMaterial({ map: texture(c) });
    })();
    const hand = (parent, w, len, z, color) => {
      const pivot = new THREE.Group();
      put(pivot, new THREE.Mesh(new THREE.BoxGeometry(w, len, 0.15), unlit(color)), 0, len / 2 - 0.5, 0);
      pivot.position.z = z;
      parent.add(pivot);
      return pivot;
    };
    const faces = [0, Math.PI].map((turn) => {                               // a dial on the front and the back
      const f = new THREE.Group();
      put(f, new THREE.Mesh(new THREE.PlaneGeometry(13, 13), dial), 0, 0, 3.05);
      const hands = { hour: hand(f, 0.8, 3.8, 3.15, 0x222222), minute: hand(f, 0.5, 5.4, 3.25, 0x222222), second: hand(f, 0.25, 5.8, 3.35, 0xd03030) };
      put(f, new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.8, 0.5), unlit(0xcaa64a)), 0, 0, 3.3);
      f.position.y = cy; f.rotation.y = turn;
      group.add(f);
      return hands;
    });
    const gears = [[-8, 8, 3, 8, 'gold_block', 0.6], [-3.6, 9.6, 1.8, 6, 'era_steel', -1], [8, 8, 3, 8, 'era_steel', -0.6], [3.6, 9.6, 1.8, 6, 'gold_block', 1],
      [-8, -8, 2.6, 8, 'era_steel', 0.5], [8, -8, 2.6, 8, 'gold_block', -0.5]].map(([x, z, r, teeth, name, speed]) => {
      const gr = gear(r, teeth, name);
      gr.position.set(x, 1.5, z);
      group.add(gr);
      return { gr, speed };
    });
    const pendulum = new THREE.Group();
    put(pendulum, box(0.4, 12, 0.4, 'era_steel'), 0, -6, 0);
    put(pendulum, box(2.4, 2.4, 1, 'gold_block'), 0, -12, 0);
    pendulum.position.set(0, 20, 4.8);
    group.add(pendulum);
    const ny = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const start = ny.getHours() * 3600 + ny.getMinutes() * 60 + ny.getSeconds(), t0 = performance.now();
    return { y: 39, tick(time, dt) {
      const s = start + (performance.now() - t0) / 1000, turn = Math.PI * 2;
      for (const h of faces) {
        h.second.rotation.z = -(Math.floor(s) % 60) / 60 * turn;             // it ticks
        h.minute.rotation.z = -((s / 3600) % 1) * turn;
        h.hour.rotation.z = -((s / 43200) % 1) * turn;
      }
      for (const { gr, speed } of gears) gr.rotation.y += dt * speed;
      pendulum.rotation.z = Math.sin(s * Math.PI) * 0.3;
    } };
  },

  // Glowing, godly: a quartz and gold temple with a floating crown, a halo, wings and orbiting lights.
  Aura(group) {
    put(group, box(24, 1, 24, 'quartz_block_side'), 0, 0.5, 0);
    put(group, box(18, 1, 18, 'gold_block'), 0, 1.5, 0);
    let y = 2;
    for (const [w, h] of [[10, 12], [8, 10], [6, 8]]) {
      put(group, box(w, h, w, wall('quartz_block_side', w / B, h / B, 'glowstone', 0.85), 'quartz_block_side'), 0, y + h / 2, 0);
      put(group, box(w + B, 1, w + B, 'gold_block'), 0, y + h - 0.5, 0);
      y += h;
    }
    put(group, new THREE.Mesh(new THREE.BoxGeometry(2, 3, 2), unlit(0xfff6c8)), 0, y + 1, 0);
    y += 2;
    const crown = new THREE.Group();                                           // the top floats
    put(crown, box(4, 6, 4, wall('quartz_block_side', 2, 3, 'glowstone', 1), 'gold_block'), 0, 3, 0);
    put(crown, box(6, 1, 6, 'gold_block'), 0, 5.5, 0);
    crown.position.y = y;
    group.add(crown);
    const crownY = y;
    y += 6;
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {                    // temple columns
      put(group, box(2, 12, 2, 'quartz_block_side'), sx * 10, 7, sz * 10);
      put(group, box(3, 1, 3, 'gold_block'), sx * 10, 13.5, sz * 10);
      put(group, box(2, 2, 2, 'glowstone'), sx * 10, 15, sz * 10);
    }
    const gold = unlit(0xffe27a);
    const halo = ring(5, 14, 1, gold);
    halo.position.y = y + 4.6;
    group.add(halo);
    const wings = [-1, 1].map((sx) => {
      const wing = new THREE.Group();
      [10, 8, 6, 4].forEach((len, i) => put(wing, box(len, 1.2, 1, 'white_wool'), sx * len / 2, -i * 1.6, 0));
      wing.position.set(sx * 3, 28, -2);
      group.add(wing);
      return { wing, sx };
    });
    const orbs = Array.from({ length: 6 }, (_, i) => ({ i,
      m: put(group, new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.2, 1.2), glow(0xffd766, 0.9)), 0, 0, 0) }));
    const haze = [[14, 0.07], [19, 0.04]].map(([w, o]) => ({ o,
      m: put(group, new THREE.Mesh(new THREE.BoxGeometry(w, 56, w), glow(0xfff0b0, o)), 0, 28, 0) }));
    return { y: y + 0.4, tick(time, dt) {
      crown.position.y = crownY + Math.sin(time * 1.1) * 0.3;
      halo.rotation.y += dt * 0.9;
      halo.position.y = y + 4.6 + Math.sin(time * 1.1) * 0.3;
      for (const { wing, sx } of wings) wing.rotation.z = sx * (0.35 + 0.12 * Math.sin(time * 1.4));
      for (const { m, i } of orbs) {
        const a = time * 0.6 + (i / 6) * Math.PI * 2;
        m.position.set(Math.cos(a) * 10, 14 + i * 3.4 + Math.sin(time * 1.5 + i) * 1.2, Math.sin(a) * 10);
        m.rotation.y = time;
      }
      for (const h of haze) h.m.material.opacity = h.o * (0.75 + 0.25 * Math.sin(time * 1.3));
      gold.color.setHex(0xffe27a).multiplyScalar(0.85 + 0.15 * Math.sin(time * 2));
    } };
  },
};

function fish(color) {                                 // swims toward its own +x
  const f = new THREE.Group(), m = new THREE.MeshLambertMaterial({ color });
  put(f, new THREE.Mesh(new THREE.BoxGeometry(1.6, 1, 0.6), m), 0, 0, 0);
  put(f, new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.2, 0.3), m), -1.1, 0, 0);
  return f;
}

function gear(r, teeth, name) {                        // lies flat, turns about y
  const g = new THREE.Group();
  put(g, box(r * 1.3, 1, r * 1.3, name), 0, 0, 0);
  put(g, box(r * 1.3, 1, r * 1.3, name), 0, 0, 0).rotation.y = Math.PI / 4;
  for (let i = 0; i < teeth; i++) {
    const a = (i / teeth) * Math.PI * 2;
    put(g, box(1, 1, 1, name), Math.cos(a) * r, 0, Math.sin(a) * r).rotation.y = -a;
  }
  return g;
}

// An agent the city has no theme for gets a plain brick tower.
function plainTower(group) {
  put(group, box(24, 1, 24, 'cobblestone'), 0, 0.5, 0);
  let y = 1;
  for (const [w, h] of [[10, 14], [8, 10], [6, 8], [4, 6]]) {
    put(group, box(w, h, w, wall('bricks', w / B, h / B, 'glowstone'), 'oak_planks'), 0, y + h / 2, 0);
    put(group, box(w + B, 1, w + B, 'oak_planks'), 0, y + h - 0.5, 0);
    y += h;
  }
  return { y, tick() {} };
}
const STATUS = Object.fromEntries(['sea_lantern', 'gold_block', 'emerald_block', 'redstone_block', 'obsidian', 'iron_block']
  .map((n) => [n, texture(tile(n))]));

function makeSign(className) {
  const el = document.createElement('div');
  el.className = className;
  labels.appendChild(el);
  return el;
}
const vaultSign = makeSign('sign vault');

function createTower(id, index, count) {
  const group = new THREE.Group();
  const spread = count > 1 ? (index / (count - 1) - 0.5) * 2 : 0;    // -1..1
  group.position.set(spread * 60, 0, -18 - (1 - Math.abs(spread)) * 12);
  const { y, tick } = (BUILDERS[id] || plainTower)(group);

  const status = put(group, new THREE.Mesh(new THREE.BoxGeometry(B + 1, B + 1, B + 1),
    new THREE.MeshLambertMaterial({ map: STATUS.sea_lantern })), 0, y + 1.5, 0);
  put(group, box(B, B, B, 'beacon'), 0, y + 4, 0);
  const top = y + 5;
  const beam = put(group, new THREE.Mesh(new THREE.BoxGeometry(1, 180, 1), glow(GREEN, 0.9)), 0, top + 90, 0);
  beam.visible = false;
  const burst = put(group, new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), glow(GREEN, 0)), 0, top, 0);
  scene.add(group);

  const sign = makeSign('sign');
  sign.dataset.theme = id;
  sign.addEventListener('click', () => enterTower(id));
  const tower = { id, group, status, beam, burst, sign, top, tick, fire: 0, burstT: 1, bot: null, lift: index % 2 ? 11 : 0 };   // neighbours' signs sit at two heights
  group.traverse((o) => { o.userData.bot = id; });
  towers.set(id, tower);
  return tower;
}

function syncTowers(bots) {
  const ids = bots.map((b) => b.id).join();
  if (ids === [...towers.keys()].join()) return;
  for (const t of towers.values()) { scene.remove(t.group); t.sign.remove(); }   // roster changed
  towers.clear();
  bots.forEach((b, i) => createTower(b.id, i, bots.length));
  if (inside && !towers.has(inside)) exitTower();
}

function sideColor(side) { return side === 'PUT' ? RED : GREEN; }
// CALL (green) for a long view, PUT (red) for a short one: from the open position, else the latest signal.
const sideOf = (bot) => ((bot.position?.direction || bot.signal?.direction) === 'SHORT' ? 'PUT' : 'CALL');

function animateTower(t, time, dt) {
  const bot = t.bot;
  if (!bot) return;
  const side = sideOf(bot);
  const color = sideColor(side);
  t.fire = Math.max(0, t.fire - dt * 1.4);
  let width = 0, opacity = 0, block = bot.state === 'STANDBY' || !(bot.regime?.size > 0) ? 'iron_block' : 'sea_lantern';
  if (bot.state === 'ARMED') {            // charging
    width = 0.5 + 0.2 * Math.sin(time * 9);
    opacity = 0.45 + 0.25 * Math.sin(time * 9);
    block = 'gold_block';
  } else if (bot.state === 'IN_TRADE') {  // fired: the beacon is lit
    width = 1.6 + 0.1 * Math.sin(time * 5);
    opacity = 0.85;
    block = side === 'PUT' ? 'redstone_block' : 'emerald_block';
  } else if (bot.state === 'LOCKED') {
    block = 'obsidian';
  }
  width += t.fire * 5;
  t.beam.visible = width > 0.01;
  t.beam.scale.set(Math.max(width, 0.01), 1, Math.max(width, 0.01));
  t.beam.material.color.setHex(color);
  t.beam.material.opacity = Math.min(1, opacity + t.fire);
  t.status.material.map = STATUS[block];
  if (t.burstT < 1) {                     // trade just closed: green or red shockwave
    t.burstT = Math.min(1, t.burstT + dt * 0.7);
    t.burst.scale.setScalar(1 + t.burstT * 14);
    t.burst.material.opacity = 0.55 * (1 - t.burstT);
  }
}

// ---------------------------------------------------------------- hud

let state = null;
let selected = null;
let night = (() => { try { return localStorage.getItem('whynot-night') ?? (matchMedia('(prefers-color-scheme: dark)').matches ? '1' : '0'); } catch { return '0'; } })() === '1';
const pct = (v, d = 1) => (v == null ? '-' : (v * 100).toFixed(d) + '%');
const cash = (v) => (v == null ? '-' : money(v));
const tag = (text, kind = '') => `<span class="tag ${kind}">${esc(text)}</span>`;
const table = (headers, rows, empty = 'Nothing yet.') => `<table><tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr>${rows.join('') ||
  `<tr><td colspan="${headers.length}" class="dim">${empty}</td></tr>`}</table>`;
// SIMULATED or BROKER: every result on the page says which.
const origin = (simulated, label) => tag(simulated ? 'SIMULATED' : (label || 'BROKER').toUpperCase(), simulated ? 'gold' : 'up');

function statusLine(bot) {
  if (bot.state === 'STANDBY') return 'NOT TRADING';
  if (bot.state === 'LOCKED') return 'TRADING HALTED';
  if (bot.state === 'IN_TRADE') return `${bot.position.contract} ${bot.position.unrealized == null ? '' : money(bot.position.unrealized)}`;
  if (bot.state === 'ARMED') return 'ORDER WAITING FOR THE OPEN';
  return bot.signal?.action ? `LAST DECISION: ${bot.signal.action}` : 'WATCHING';
}

function updateSigns() {
  for (const bot of state.bots) {
    const t = towers.get(bot.id);
    if (t.bot && bot.position && !t.bot.position) t.fire = 1;                       // a position just opened: the beacon flares
    if (t.bot && bot.trades > t.bot.trades) { t.burstT = 0; t.burst.material.color.setHex(bot.total_pnl >= t.bot.total_pnl ? GREEN : RED); }
    t.bot = bot;
    t.sign.dataset.state = bot.state;
    t.sign.dataset.side = sideOf(bot);
    // The sign stays small so five fit side by side; pointing at it shows the rest, and clicking opens everything.
    t.sign.innerHTML = `<b>${esc(bot.name)}</b>
      <div class="state">${bot.symbol} &middot; ${esc(bot.regime.active || 'NO STATE YET')}</div>
      <div class="state hi">${esc(statusLine(bot))}</div>
      ${bot.state === 'STANDBY' ? '' : `<div>TODAY <span class="${cls(bot.day_pnl)}">${money(bot.day_pnl)}</span> &middot; ALL <span class="${cls(bot.total_pnl)}">${money(bot.total_pnl)}</span></div>`}
      <div class="more"><div>STRATEGY ${esc(bot.strategy.version)} (${esc(bot.strategy.status)})</div>
        <div>SIGNAL ${esc(bot.signal.action || 'NONE YET')}</div><div>POSITION ${bot.position ? esc(bot.position.contract) : 'NONE'}</div>
        <div>${bot.win_rate == null ? 'NO TRADES YET' : pct(bot.win_rate, 0) + ' WINS OF ' + bot.trades} &middot; DRAWDOWN ${pct(bot.drawdown)}</div></div>`;
  }
  const v = state.vault;
  vaultSign.classList.toggle('dd', !!v.halted || v.total_pnl < 0);
  vaultSign.innerHTML = state.standby
    ? `<div class="title">THE VAULT &middot; ${state.bots.length} AGENTS</div><div>NOT TRADING YET</div>`
    : `<div class="title">THE VAULT &middot; PAPER MONEY &middot; ${esc((v.broker || '').toUpperCase())}</div>
       <div class="eq">${v.equity == null ? 'BROKER NOT REACHABLE' : usd(v.equity)}</div>
       <div>TODAY <span class="${cls(v.day_pnl ?? 0)}">${cash(v.day_pnl)}</span> &middot; ALL <span class="${cls(v.total_pnl ?? 0)}">${cash(v.total_pnl)}</span></div>
       <div>${v.trades} TRADES${v.win_rate == null ? '' : ' &middot; ' + pct(v.win_rate, 0) + ' WON'}${v.halted ? ' &middot; <span class="down">HALTED</span>' : ''}</div>`;
}

function updateHud() {
  const offline = !!state.offline;
  $('clock').textContent = state.ts ? state.ts.slice(11, 16) + ' ET' : '--:--';
  $('date').textContent = offline ? 'DASHBOARD SERVER NOT RUNNING' : state.standby ? 'STANDBY - NOT PAPER TRADING'
    : `PAPER TRADING &middot; LAST CLOSE PROCESSED: ${state.last_close || 'NONE YET'}`.replace(/&middot;/g, '·');
  $('badge-source').textContent = offline ? 'OFFLINE' : state.mode.replace(/_/g, ' ');
  $('lock-banner').hidden = !state.vault.halted;
  $('lock-banner').textContent = state.vault.halted ? `TRADING HALTED: ${state.vault.halt_reason}` : '';
  const seen = new Set();
  $('regime').innerHTML = state.bots.filter((b) => !seen.has(b.symbol) && seen.add(b.symbol)).map((b) => {
    const r = b.regime, p = r.probs?.[r.active] ?? 0;
    const size = r.frozen ? 'FROZEN' : !(r.size > 0) ? 'FLAT' : r.size < 1 ? 'HALF' : 'FULL';
    return `<div class="prob on"><span>${b.symbol}</span><i><u style="width:${(p * 100).toFixed(0)}%"></u></i><span>${(p * 100).toFixed(0)}%</span></div>
      <div class="plan"><span><b>${esc(r.active || 'no state yet')}</b>${r.expected_days ? ' &middot; lasts ~' + Math.round(r.expected_days) + 'd' : ''}</span><span class="${size === 'FULL' ? '' : 'down'}">${size} SIZE</span></div>`;
  }).join('');
  $('tape').innerHTML = state.bots.filter((b) => b.signal?.action).map((b) =>
    `<span><b>${b.id.toUpperCase()}</b> ${esc(b.signal.day || '')} ${esc(b.signal.action)}: ${esc(b.signal.reason || '')}</span>`).join('');
}

// ---------------------------------------------------------------- the tower panel

function openPanel(id) { selected = id; $('panel').hidden = false; document.body.classList.add('book'); renderPanel(); loadAgentTrades(); }
function closePanel() { selected = null; $('panel').hidden = true; document.body.classList.remove('book'); }
$('panel-close').addEventListener('click', closePanel);
let agentTrades = { id: null, rows: [] };
async function loadAgentTrades() {
  if (!selected) return;
  const id = selected;
  try { agentTrades = { id, rows: (await (await fetch('/api/journal?agent=' + id)).json()).rows }; } catch { agentTrades = { id, rows: [] }; }
  if (selected === id) { renderPanel(); if (inside) drawMonitors(); }
}

function probBars(r) {
  return Object.entries(r.probs || {}).map(([s, p]) =>
    `<div class="prob ${s === r.active ? 'on' : ''}"><span>${s}</span><i><u style="width:${(p * 100).toFixed(0)}%"></u></i><span>${(p * 100).toFixed(0)}%</span></div>`).join('');
}

function renderPanel() {
  const bot = state?.bots.find((b) => b.id === selected);
  if (!bot) return;
  const p = bot.position, r = bot.regime;
  const mine = agentTrades.id === bot.id ? agentTrades.rows : [];
  const paper = mine.filter((t) => !t.source.includes('acktest'));
  $('panel-body').innerHTML = `<h2>${esc(bot.name)} ${tag(bot.status, bot.state === 'STANDBY' ? '' : 'up')}</h2>
    <div class="hyp">${esc(bot.bot)} &middot; trades ${bot.symbol} options &middot; ${esc(bot.focus)} &middot; ${esc(bot.theme)} tower</div>
    ${bot.status_note ? `<div class="callout">${esc(bot.status_note)}</div>` : ''}
    <h3>RIGHT NOW</h3>
    <div class="row"><span>Running status</span><span>${esc(statusLine(bot))}</span></div>
    <div class="row"><span>Current strategy</span><span>${esc(bot.strategy.version)} (${esc(bot.strategy.status)}: not yet proven)</span></div>
    <div class="row"><span>Current signal</span><span>${esc(bot.signal.action || 'none yet')}${bot.signal.day ? ' on ' + bot.signal.day : ''}</span></div>
    <div class="note">${esc(bot.signal.reason || '')}</div>
    <div class="row"><span>Open position</span><span>${p ? `${p.qty} x ${esc(p.contract)} at ${p.entry_price} ${origin(p.simulated, 'PAPER BROKER')}` : 'none'}</span></div>
    ${p ? `<div class="row"><span>Position result so far</span><span class="${cls(p.unrealized ?? 0)}">${cash(p.unrealized)}</span></div>` : ''}
    <div class="tiles">
      <div><b class="${cls(bot.day_pnl)}">${money(bot.day_pnl)}</b>today</div>
      <div><b class="${cls(bot.total_pnl)}">${money(bot.total_pnl)}</b>since paper trading began</div>
      <div><b>${bot.win_rate == null ? '-' : pct(bot.win_rate, 0)}</b>win rate (${bot.trades} trades)</div>
      <div><b>${pct(bot.drawdown)}</b>drawdown of its $20,000 share</div>
    </div>
    <h3>MARKET REGIME FOR ${bot.symbol} (as of the close of ${esc(r.day || '-')})</h3>
    <div class="row"><span>Active state</span><span>${esc(r.active || 'none yet')}${r.expected_days ? ', typically lasts ' + Math.round(r.expected_days) + ' days' : ''}</span></div>
    <div class="note">${esc(r.reason || '')}</div>${probBars(r)}
    <h3>PAPER TRADES ${paper.length ? '' : '(none yet)'}</h3>
    ${table(['Out', 'Contract', 'Result', 'Why it ended', ''], paper.slice(0, 12).map((t) => `<tr><td>${t.exit_day}</td><td>${esc(t.instrument)}</td>
      <td class="${cls(t.pnl)}">${money(t.pnl)}</td><td>${esc(t.exit_reason || '')}</td><td>${origin(t.simulated, 'PAPER BROKER')}</td></tr>`), 'No finished paper trades yet.')}
    <div class="note">Backtest history for this agent is in TRADE JOURNAL and BACKTEST LAB. Its playbooks are in the project's playbooks folder.</div>`;
}

// ---------------------------------------------------------------- inside a tower

let inside = null;
const room = new THREE.Scene();
room.background = new THREE.Color(0x1b1410);
room.fog = new THREE.Fog(0x1b1410, 50, 130);
const roomCam = new THREE.PerspectiveCamera(48, 1, 0.1, 300);
room.add(new THREE.HemisphereLight(0xfff0d0, 0x3a2a1a, 2.2));
const deskLight = new THREE.PointLight(0xffd9a0, 60, 40);
deskLight.position.set(0, 8, 2);
room.add(deskLight);
const fillLight = new THREE.PointLight(0xffffff, 260, 70);      // lights the golem's back, which faces the camera
fillLight.position.set(8, 10, 16);
room.add(fillLight);
const std = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.55, ...extra });

function screen(w, h, cw, ch) {
  const canvas = document.createElement('canvas');
  canvas.width = cw; canvas.height = ch;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const group = new THREE.Group();
  group.add(box(w + 0.4, h + 0.4, 0.3, 'coal_block'));
  const face = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }));
  face.position.z = 0.16;
  group.add(face);
  room.add(group);
  return { group, canvas, texture, g: canvas.getContext('2d') };
}

const monitors = {
  main: screen(6.4, 3.6, 1280, 720),
  left: screen(3.4, 3.0, 680, 600),
  right: screen(3.4, 3.0, 680, 600),
};
monitors.main.group.position.set(0, 4.8, -3);
monitors.left.group.position.set(-5.4, 4.5, -1.6);
monitors.left.group.rotation.y = 0.55;
monitors.right.group.position.set(5.4, 4.5, -1.6);
monitors.right.group.rotation.y = -0.55;

const robot = new THREE.Group();
{
  // the room: plank floor, stone-brick walls, a wall of bookshelves, torches
  put(room, box(60, 1, 60, 'oak_planks'), 0, -0.5, 0);
  put(room, box(60, 22, B, 'stone_bricks'), 0, 11, -12);
  put(room, box(B, 22, 60, 'stone_bricks'), -24, 11, 0);
  put(room, box(B, 22, 60, 'stone_bricks'), 24, 11, 0);
  put(room, box(36, 8, B, 'bookshelf', 'oak_planks'), 0, 4, -10.5);                 // the strategy library
  put(room, box(36, 8, B, 'bookshelf', 'oak_planks'), 0, 16, -10.5);
  for (const x of [-21, 21]) {
    put(room, box(B, B, B, 'glowstone'), x, 13, -10.5);
    const torch = new THREE.PointLight(0xffb060, 40, 34);
    torch.position.set(x * 0.9, 13, -8);
    room.add(torch);
  }
  put(room, box(4, 4, 4, 'crafting_table_side', 'crafting_table_top'), -14, 2, -6);
  put(room, box(4, 4, 4, 'gold_block'), 14, 2, -6);
  put(room, box(11, 0.5, 3.4, 'spruce_planks'), 0, 2.25, -1.2);                     // the desk
  for (const x of [-5, 5]) put(room, box(0.6, 2, 3, 'oak_log', 'oak_log_top'), x, 1, -1.2);
  for (const [x, name] of [[-2.6, 'redstone_block'], [2.6, 'emerald_block']]) {     // PUT and CALL buttons
    const button = put(room, box(1.1, 0.5, 1.1, name), x, 2.75, -0.6);
    robot.userData[name === 'emerald_block' ? 'callBtn' : 'putBtn'] = button;
  }

  // the trader: an iron golem at the desk
  const face = (() => {
    const c = document.createElement('canvas');
    c.width = c.height = 16;
    const g = c.getContext('2d');
    g.drawImage(tile('iron_block'), 0, 0);
    g.fillStyle = '#3a1010'; g.fillRect(3, 6, 3, 2); g.fillRect(10, 6, 3, 2);       // eyes
    g.fillStyle = '#7a6a5a'; g.fillRect(7, 8, 2, 4);                                // nose
    return c;
  })();
  const body = put(robot, box(2.2, 2.4, 1.4, 'iron_block'), 0, 2.4, 0);
  const stripe = put(robot, new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.1), new THREE.MeshBasicMaterial({ color: 0xffffff })), 0, 2.7, -0.76);
  const head = new THREE.Group();
  head.position.y = 4.45;
  const skull = box(1.7, 1.7, 1.7, 'iron_block');
  skull.material[5] = lambert(face);                     // the face looks at the screens (-z)
  head.add(skull);
  const bulb = put(head, new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), new THREE.MeshBasicMaterial({ color: BLUE })), 0, 1.15, 0);
  const hands = [-1, 1].map((sideX) => put(robot, box(0.7, 0.7, 1.6, 'iron_block'), sideX * 1.5, 2.75, -1.1));
  robot.add(head);
  robot.position.set(0, 0, 1.4);
  Object.assign(robot.userData, { head, bulb, stripe, hands, jump: null, body });
  room.add(robot);

  // a glowing block that hovers around the desk
  const drone = new THREE.Group();
  drone.add(box(0.7, 0.7, 0.7, 'sea_lantern'));
  room.add(drone);
  robot.userData.drone = drone;
}

function enterTower(id) {
  inside = id;
  const [bg, light] = ROSTER.find((r) => r.id === id)?.room || [0x1b1410, 0xffd9a0];   // the room takes the tower's colours
  room.background.setHex(bg); room.fog.color.setHex(bg); deskLight.color.setHex(light);
  renderPass.scene = room;
  renderPass.camera = roomCam;
  labels.hidden = true;
  $('exit-tower').hidden = false;
  openPanel(id);
  drawMonitors();
}
function exitTower() {
  inside = null;
  renderPass.scene = scene;
  renderPass.camera = camera;
  labels.hidden = false;
  $('exit-tower').hidden = true;
  closePanel();
}
$('exit-tower').addEventListener('click', exitTower);

function animateRoom(time, dt) {
  const bot = state?.bots.find((b) => b.id === inside);
  if (!bot) return;
  const u = robot.userData;
  const side = sideOf(bot);
  const asleep = bot.state === 'LOCKED';
  const busy = bot.state === 'IN_TRADE' ? 14 : bot.state === 'ARMED' ? 9 : 4;
  roomCam.position.set(9 + Math.sin(time * 0.25) * 0.6, 7.6, 15);
  roomCam.lookAt(4.6, 4.1, -2);
  u.head.rotation.x = asleep ? 0.45 : Math.sin(time * 1.3) * 0.05;
  u.head.rotation.y = asleep ? 0 : Math.sin(time * 0.6) * 0.35;      // looks across the screens
  u.head.position.y = 4.45 + (asleep ? -0.1 + Math.sin(time * 1.5) * 0.04 : Math.sin(time * 2.2) * 0.05);
  u.hands.forEach((hand, i) => {
    hand.position.y = asleep ? 2.72 : 2.78 + Math.abs(Math.sin(time * busy + i * 1.7)) * 0.28;
  });
  // reaching for the button it is about to press
  const reach = bot.state === 'ARMED' || bot.state === 'IN_TRADE' ? 1 : 0;
  u.hands[1].position.x += ((side === 'PUT' ? 1.5 + reach * 1.1 : 1.5) - u.hands[1].position.x) * 0.1;
  u.hands[0].position.x += ((side === 'CALL' ? -1.5 - reach * 1.1 : -1.5) - u.hands[0].position.x) * 0.1;
  const color = asleep ? 0x555a88 : bot.state === 'SCANNING' || bot.state === 'STANDBY' ? BLUE : bot.state === 'ARMED' ? GOLD : sideColor(side);
  const pulse = bot.state === 'ARMED' ? 0.6 + 0.4 * Math.sin(time * 10) : 1;
  u.bulb.material.color.setHex(color).multiplyScalar(pulse);
  u.stripe.material.color.setHex(bot.state === 'IN_TRADE' ? sideColor(side) : 0xffffff);
  u.callBtn.scale.y = bot.state === 'IN_TRADE' && side === 'CALL' ? 0.4 : 1;
  u.putBtn.scale.y = bot.state === 'IN_TRADE' && side === 'PUT' ? 0.4 : 1;
  if (u.jump) {                             // a trade just closed: hop on a win, slump on a loss
    u.jump.t += dt * 2.2;
    robot.position.y = u.jump.win ? Math.max(0, Math.sin(u.jump.t * Math.PI)) * 0.9 : 0;
    u.stripe.material.color.setHex(u.jump.win ? GREEN : RED);
    if (u.jump.t >= 1) { u.jump = null; robot.position.y = 0; }
  }
  u.drone.position.set(-3.6 + Math.sin(time * 0.7) * 1.2, 6.4 + Math.sin(time * 1.9) * 0.25, 0.5 + Math.cos(time * 0.7) * 1.2);
  u.drone.rotation.y = time * 2;
}

function drawMonitors() {
  const bot = state?.bots.find((b) => b.id === inside);
  if (!bot) return;
  const mono = (px) => `${px}px Monocraft, ui-monospace, Menlo, monospace`;
  const base = (m) => { m.g.fillStyle = '#10100f'; m.g.fillRect(0, 0, m.canvas.width, m.canvas.height); m.g.textBaseline = 'top'; m.g.textAlign = 'left'; return m.g; };
  const colour = { ARMED: '#ffaa00', IN_TRADE: sideOf(bot) === 'PUT' ? '#ff5555' : '#55ff55', LOCKED: '#ff5555', STANDBY: '#aaaaaa' }[bot.state] || '#55ffff';

  let g = base(monitors.main);                                   // main: where it stands
  g.fillStyle = '#ffffff'; g.font = mono(40); g.fillText(`${bot.name} · ${bot.symbol}`, 28, 22);
  g.fillStyle = colour; g.textAlign = 'right'; g.fillText(bot.status, 1252, 22); g.textAlign = 'left';
  g.fillStyle = '#55ffff'; g.font = mono(64); g.fillText(bot.regime.active || 'NO STATE YET', 28, 110);
  g.fillStyle = '#aaaaaa'; g.font = mono(26); g.fillText((bot.regime.reason || '').slice(0, 70), 28, 196);
  Object.entries(bot.regime.probs || {}).forEach(([s, p], i) => {
    g.fillStyle = '#aaaaaa'; g.font = mono(26); g.fillText(s, 28, 270 + i * 54);
    g.fillStyle = '#333333'; g.fillRect(250, 274 + i * 54, 760, 22);
    g.fillStyle = s === bot.regime.active ? '#55ff55' : '#777777'; g.fillRect(250, 274 + i * 54, 760 * p, 22);
    g.fillStyle = '#ffffff'; g.fillText((p * 100).toFixed(0) + '%', 1040, 270 + i * 54);
  });
  g.fillStyle = colour; g.font = mono(30); g.fillText(statusLine(bot).slice(0, 60), 28, 640);
  monitors.main.texture.needsUpdate = true;

  g = base(monitors.left);                                       // left: the trade log
  g.fillStyle = '#ffaa00'; g.font = mono(30); g.fillText('PAPER TRADES', 24, 20);
  g.font = mono(24); g.fillStyle = bot.total_pnl >= 0 ? '#55ff55' : '#ff5555';
  g.fillText(`today ${money(bot.day_pnl)}   all ${money(bot.total_pnl)}`, 24, 64);
  const mine = (agentTrades.id === bot.id ? agentTrades.rows : []).filter((t) => !t.source.includes('acktest')).slice(0, 11);
  mine.forEach((t, i) => {
    g.fillStyle = '#ffffff'; g.fillText(`${(t.exit_day || '').slice(5)} ${t.direction === 'SHORT' ? 'PUT ' : 'CALL'}`, 24, 116 + i * 42);
    g.fillStyle = t.pnl >= 0 ? '#55ff55' : '#ff5555'; g.fillText(money(t.pnl), 300, 116 + i * 42);
  });
  if (!mine.length) { g.fillStyle = '#aaaaaa'; g.fillText('no finished trades yet', 24, 116); }
  monitors.left.texture.needsUpdate = true;

  g = base(monitors.right);                                      // right: who this is
  g.fillStyle = '#55ffff'; g.font = mono(34); g.fillText(bot.name, 24, 20);
  g.fillStyle = '#ffaa00'; g.font = mono(24); g.fillText(`${bot.strategy.version} (${bot.strategy.status})`, 24, 76);
  g.fillStyle = '#ffffff'; g.font = mono(20);
  [`wins: ${bot.win_rate == null ? '-' : pct(bot.win_rate, 0)} of ${bot.trades}`, `drawdown: ${pct(bot.drawdown)}`,
    `signal: ${bot.signal.action || 'none yet'}`, `position: ${bot.position ? bot.position.contract : 'none'}`, '', ...wrap(bot.focus, 30)]
    .forEach((line, i) => g.fillText(line, 24, 124 + i * 32));
  g.fillStyle = '#ffaa00'; g.fillText('PAPER MONEY ONLY', 24, 540);
  monitors.right.texture.needsUpdate = true;
}
const wrap = (text, width) => text.split(' ').reduce((lines, w) => {
  const last = lines[lines.length - 1];
  if ((last + ' ' + w).trim().length > width) lines.push(w); else lines[lines.length - 1] = (last + ' ' + w).trim();
  return lines;
}, ['']);

// ---------------------------------------------------------------- the command center sections

const SECTIONS = [['overview', 'PORTFOLIO'], ['live', 'LIVE TRADING'], ['regimes', 'MARKET REGIMES'], ['replay', 'BACKTEST LAB'], ['strategy', 'STRATEGY RESEARCH'],
  ['journal', 'TRADE JOURNAL'], ['risk', 'RISK CENTER'], ['health', 'DATA HEALTH'], ['ai', 'AI RESEARCH']];
let section = null, sectionTimer = null;
$('nav').innerHTML = SECTIONS.map(([k, label]) => `<button data-section="${k}">${label}</button>`).join('') + '<button id="btn-night"></button>';
$('nav').addEventListener('click', (e) => { const k = e.target.dataset?.section; if (k) openSection(section === k ? null : k); });
$('lab-close').addEventListener('click', () => openSection(null));
$('btn-night').addEventListener('click', () => { night = !night; try { localStorage.setItem('whynot-night', night ? '1' : '0'); } catch { /* private mode */ } applyNight(); });
function applyNight() { $('btn-night').textContent = night ? 'DAY' : 'NIGHT'; document.body.classList.toggle('night', night); }
applyNight();

function openSection(k) {
  section = k;
  clearInterval(sectionTimer);
  $('lab').hidden = !k;
  for (const b of $('nav').querySelectorAll('[data-section]')) b.classList.toggle('active', b.dataset.section === k);
  if (!k) return;
  $('lab-body').innerHTML = '<div class="dim">Loading...</div>';
  renderSection();
  if (k !== 'replay' && k !== 'journal') sectionTimer = setInterval(renderSection, 5000);
}
const get = async (url) => { const r = await fetch(url); const body = await r.json(); if (!r.ok) throw new Error(body.error || r.statusText); return body; };
async function renderSection() {
  const k = section, body = $('lab-body');
  try {
    const html = await RENDER[k]();
    if (section === k && html != null) { const y = $('lab').scrollTop; body.innerHTML = html; $('lab').scrollTop = y; }
  } catch (err) {
    if (section === k) body.innerHTML = `<h2>${k.toUpperCase()}</h2><div class="callout">Could not load this section: ${esc(err.message)}. Is the dashboard server running?</div>`;
  }
}
const when = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString() : '-');
const bars = (probs, active) => probBars({ probs, active });

const RENDER = {
  async overview() {
    const s = state, v = s.vault;
    return `<h2>PORTFOLIO OVERVIEW</h2>
      <div class="callout">Paper money on the ${esc(v.broker || 'broker')}. Nothing here is real money. ${esc(v.note || '')}</div>
      <div class="tiles"><div><b>${v.equity == null ? '-' : usd(v.equity)}</b>account value</div>
        <div><b class="${cls(v.day_pnl ?? 0)}">${cash(v.day_pnl)}</b>today</div><div><b class="${cls(v.total_pnl ?? 0)}">${cash(v.total_pnl)}</b>since the $100,000 start</div>
        <div><b>${v.trades}</b>finished trades</div><div><b>${v.win_rate == null ? '-' : pct(v.win_rate, 0)}</b>won</div>
        <div><b class="${v.halted ? 'down' : 'up'}">${v.halted ? 'HALTED' : 'ALLOWED'}</b>trading</div></div>
      <h3>THE FIVE AGENTS</h3>
      ${table(['Agent', 'Fund', 'Regime', 'Strategy', 'Status', 'Signal', 'Open position', 'Today', 'All', 'Wins', 'Drawdown'], s.bots.map((b) => `<tr>
        <td><b>${b.id}</b></td><td>${b.symbol}</td><td>${esc(b.regime.active || '-')}</td><td>${esc(b.strategy.version)}</td><td>${esc(b.status)}</td>
        <td>${esc(b.signal.action || '-')}</td><td>${b.position ? esc(b.position.contract) : '-'}</td><td class="${cls(b.day_pnl)}">${money(b.day_pnl)}</td>
        <td class="${cls(b.total_pnl)}">${money(b.total_pnl)}</td><td>${b.win_rate == null ? '-' : pct(b.win_rate, 0)}</td><td>${pct(b.drawdown)}</td></tr>`))}
      <div class="note">All five strategies are version one and provisional: none has passed the acceptance gates. They trade at probe size, about one contract a trade.</div>`;
  },
  async live() {
    const d = await get('/api/live');
    return `<h2>LIVE TRADING</h2><div class="note">Decisions are made after each close; orders go in at the next open and are cancelled if not filled by 10:30 New York time.</div>
      <h3>LATEST DECISIONS</h3>${table(['Day', 'Agent', 'Decision', 'State', 'Why'], d.decisions.map((x) => `<tr><td>${x.day}</td><td><b>${x.agent}</b></td>
        <td>${esc(x.action)} ${esc(x.direction || '')}</td><td>${esc(x.state || '-')}</td><td class="wrap">${esc(x.reason || '')}</td></tr>`), 'No decisions yet. The first come after the next close.')}
      <h3>ORDERS WAITING</h3>${table(['Decided', 'Agent', 'Order', 'Contract', 'Status', 'Why'], d.waiting.map((x) => `<tr><td>${x.decided_day}</td><td><b>${x.agent}</b></td>
        <td>${x.side} ${x.qty ?? ''}</td><td>${esc(x.contract || '-')}</td><td>${esc(x.status)}</td><td class="wrap">${esc(x.reason || '')}</td></tr>`), 'No orders waiting.')}
      <h3>OPEN POSITIONS</h3>${table(['Agent', 'Contract', 'Qty', 'In', 'Price', ''], d.positions.map((x) => `<tr><td><b>${x.agent}</b></td><td>${esc(x.contract)}</td><td>${x.qty}</td>
        <td>${x.entry_day}</td><td>${x.entry_price}</td><td>${origin(!!x.simulated, 'PAPER BROKER')}</td></tr>`), 'No open positions.')}
      <h3>FILLS AND FINISHED TRADES</h3>${table(['Out', 'Agent', 'Contract', 'In at', 'Out at', 'Result', 'Why', ''], d.trades.map((x) => `<tr><td>${x.exit_day}</td><td><b>${x.agent}</b></td>
        <td>${esc(x.contract)}</td><td>${x.entry_price}</td><td>${x.exit_price}</td><td class="${cls(x.pnl)}">${money(x.pnl)}</td><td class="wrap">${esc(x.exit_reason || '')}</td>
        <td>${origin(!!x.simulated, 'PAPER BROKER')}</td></tr>`), 'No finished trades yet.')}
      <h3>EVERY ORDER, INCLUDING REFUSALS</h3>${table(['Decided', 'Agent', 'Order', 'Contract', 'Status', 'Why'], d.orders.map((x) => `<tr><td>${x.decided_day}</td><td><b>${x.agent}</b></td>
        <td>${x.side} ${x.qty ?? ''}</td><td>${esc(x.contract || '-')}</td><td class="${x.status === 'refused' ? 'down' : ''}">${esc(x.status)}</td><td class="wrap">${esc(x.reason || '')}</td></tr>`))}`;
  },
  async regimes() {
    const d = await get('/api/regimes');
    return `<h2>MARKET REGIMES</h2><div class="note">A Hidden Markov Model reads each fund's daily candles. Every probability uses candles up to that day only. The model advises; the switching rules in code decide.</div>` +
      Object.entries(d).map(([sym, r]) => `<h3>${sym}: ${esc(r.active || 'no state yet')} (as of the close of ${esc(r.day || '-')})</h3>
        <div class="note">${esc(r.reason || '')}${r.frozen ? ' <span class="down">New entries are frozen.</span>' : ''}</div>
        <div class="two"><div><div class="dim">Probability of each state now</div>${bars(r.probs, r.active)}</div>
          <div><div class="dim">Probability of each state tomorrow</div>${bars(r.next_probs, r.active)}</div></div>
        ${table(['State', 'Size allowed', 'Typical stay', 'Volatility (z)', ...r.states.map((x) => 'to ' + x.state)], r.states.map((x) => `<tr class="${x.state === r.active ? 'on' : ''}">
          <td>${x.state}</td><td>${pct(r.policy[x.state], 0)}</td><td>${x.expected_days} days</td><td>${x.volatility_z}</td>${r.states.map((y) => `<td>${x.to[y.state]}</td>`).join('')}</tr>`), 'No model stored.')}
        <div class="note">Model version ${r.model_version ?? '-'}, fitted through ${esc(r.fitted_through || '-')}. Since paper trading began: ${r.since_live.length} day(s), ${r.since_live.filter((x) => x.switched).length} switch(es).</div>`).join('') +
      `<div class="note">How the states behaved in history is in the regime report (development years only).</div>`;
  },
  async strategy() {
    const d = await get('/api/research');
    return `<h2>STRATEGY RESEARCH</h2>
      <h3>CHAMPIONS</h3>${table(['Agent', 'Version', 'Status', 'Approved by', 'Since'], d.champions.map((c) => `<tr><td><b>${c.agent}</b></td><td>${esc(c.version)}</td>
        <td class="${c.status === 'approved' ? 'up' : 'gold'}">${esc(c.status)}</td><td>${esc(c.approved_by || 'nobody yet')}</td><td>${when(c.since)}</td></tr>`))}
      <div class="note">Provisional means a starting strategy that has not passed the gates. Only you can promote a challenger.</div>
      <h3>CHALLENGERS AND EXPERIMENTS (limits: ${d.limits.max_experiments} a cycle, ${d.limits.max_configs} settings a hypothesis)</h3>
      ${table(['#', 'Agent', 'Challenger', 'Against', 'Tested on', 'Decision', 'Why'], d.experiments.map((e) => `<tr><td>${e.id}</td><td><b>${e.agent}</b></td><td>${esc(e.challenger_version)}</td>
        <td>${esc(e.champion_version || '-')}</td><td>${esc(e.dataset)}</td><td class="${e.decision === 'candidate' || e.decision === 'promoted' ? 'up' : e.decision === 'rejected' ? 'down' : 'dim'}">${esc(e.decision)}</td>
        <td class="wrap">${esc(e.reason || '')}</td></tr>`), 'No experiments yet.')}
      <h3>RESEARCH CYCLES</h3>${table(['#', 'Direction', 'Status', 'Outcome', 'Closed'], d.cycles.map((c) => `<tr><td>${c.id}</td><td class="wrap">${esc(c.direction)}</td><td>${esc(c.status)}</td>
        <td>${esc(c.outcome || '-')}</td><td>${when(c.closed_at)}</td></tr>`), 'No cycles yet.')}
      <div class="note">Looks at the locked holdout so far: ${d.holdout_looks}.</div>`;
  },
  async ai() {
    const d = await get('/api/research'), daily = await get('/api/daily');
    const failed = d.experiments.filter((e) => e.decision === 'rejected');
    return `<h2>AI RESEARCH</h2><div class="note">Claude proposes; code tests and grades; you approve. Claude never approves its own strategy.</div>
      <h3>WAITING FOR YOUR APPROVAL</h3>${table(['Experiment', 'Agent', 'Challenger'], d.approval_queue.map((q) => `<tr><td>${q.experiment_id}</td><td><b>${q.agent}</b></td><td>${esc(q.challenger_version)}</td></tr>`),
        'Nothing is waiting. No challenger has passed yet.')}
      <h3>FAILED HYPOTHESES</h3>${failed.map((e) => `<div class="card"><b>${e.agent}: ${esc(e.challenger_version)}</b><div>${esc(e.hypothesis)}</div>
        <div class="note">Changed: ${esc(e.change)}</div><div class="down">${esc(e.reason || '')}</div></div>`).join('') || '<div class="dim">None yet.</div>'}
      <h3>FINDINGS AND LESSONS (research notes, never trading rules)</h3>
      ${table(['Agent', 'Cause', 'Note'], d.lessons.map((l) => `<tr><td><b>${l.agent}</b></td><td>${esc(l.cause || '')}</td><td class="wrap">${esc(l.text)}</td></tr>`), 'No lessons yet.')}
      <h3>DAILY RESEARCH REPORT${daily.day ? ': ' + daily.day : ''}</h3><pre class="report">${esc(daily.text)}</pre>`;
  },
  async risk() {
    const d = await get('/api/risk'), s = d.settings, eq = d.equity || 1;
    const meter = (label, used, limit) => `<div class="prob ${used >= limit ? 'bad' : 'on'}"><span>${label}</span><i><u style="width:${Math.min(100, (used / limit) * 100).toFixed(0)}%"></u></i><span>${pct(used)} / ${pct(limit, 0)}</span></div>`;
    return `<h2>RISK CENTER</h2>
      <div class="callout ${d.halted ? 'bad' : ''}">${d.halted ? 'TRADING IS HALTED: ' + esc(d.halt_reason) : 'Trading is allowed. Every order passes these limits, and no model can override them.'}</div>
      <h3>LIMITS IN USE</h3><div class="meters">
        ${meter('Drawdown (kill switch)', d.drawdown, s.max_drawdown)}${meter('Loss today', Math.max(d.day_loss, 0), s.daily_loss_limit)}
        ${meter('Loss this week', Math.max(d.week_loss, 0), s.weekly_loss_limit)}${meter('Premium at risk', d.at_risk.total / eq, s.max_portfolio_exposure)}
        ${Object.entries(d.at_risk.by_direction).map(([k, v]) => meter(`Same direction (${k === 'SHORT' ? 'puts' : 'calls'})`, v / eq, s.max_correlated_exposure)).join('')}
        ${Object.entries(d.at_risk.by_agent).map(([k, v]) => meter(`Agent ${k}`, v / eq, s.max_bot_exposure)).join('')}</div>
      <h3>ALL SETTINGS</h3>${table(['Limit', 'Value'], Object.entries(s).map(([k, v]) => `<tr><td>${k.replace(/_/g, ' ')}</td><td>${v < 1 ? pct(v) : v}</td></tr>`))}
      <h3>KILL SWITCH</h3><div class="note">${esc(d.how_to_reset)}</div>
      <h3>RISK EVENTS</h3>${table(['Day', 'Event', 'Detail'], d.events.map((e) => `<tr><td>${esc(e.day)}</td><td class="${e.kind === 'kill' || e.kind === 'halt' ? 'down' : ''}">${esc(e.kind)}</td>
        <td class="wrap">${esc(e.reason || e.contract || '')}</td></tr>`), 'No refusals, halts or kill-switch events.')}`;
  },
  async health() {
    const d = await get('/api/health'), c = await get('/api/checks');
    return `<h2>DATA HEALTH</h2>
      <h3>HEALTH CHECKS</h3>${table(['Check', 'Result', 'Detail'], c.checks.map((x) => `<tr><td>${esc(x.name)}</td><td class="${x.ok ? 'up' : 'down'}">${x.ok ? 'ok' : 'FAILED'}</td>
        <td class="wrap">${esc(x.detail || '')}</td></tr>`))}
      <h3>ALERTS (sent by: ${esc(c.channels.join(', '))})</h3>${table(['When', 'Kind', 'What', 'Sent by'], c.alerts.map((a) => `<tr><td>${when(a.at)}</td><td>${esc(a.kind)}</td>
        <td class="wrap">${esc(a.text)}</td><td class="${a.sent_via ? '' : 'down'}">${esc(a.sent_via || 'not sent yet')}</td></tr>`), 'No alerts yet.')}
      <div class="row"><span>Mode</span><span>${esc(d.mode.replace(/_/g, ' '))}</span></div>
      <div class="row"><span>Last close processed</span><span>${esc(d.last_close_processed || 'none yet')}</span></div>
      <div class="row"><span>Option contracts with real traded bars</span><span>${d.options.contracts_with_bars.toLocaleString()} (latest ${esc(d.options.last || '-')})</span></div>
      <h3>WHAT IS STORED</h3>${table(['Series', 'Candles', 'Source', 'Kind', 'Prices', 'Latest', 'Gaps', 'Last download'], d.datasets.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td>
        <td>${x.candles}</td><td>${x.source}</td><td class="${x.kind === 'authentic' ? '' : 'gold'}">${esc(x.kind)}</td><td>${esc(x.prices)}</td><td>${esc(x.last || '-')}</td><td>${x.gaps}</td>
        <td class="${x.last_download === 'failed' ? 'down' : ''}">${esc(x.last_download || '-')}</td></tr>`))}
      <h3>WHAT THE ACCOUNT CAN AND CANNOT REACH</h3>${d.findings.map((f) => `<div class="journal">${esc(f.text)}</div>`).join('')}
      <h3>RECENT ACTIVITY</h3>${table(['When', 'What', 'Detail'], d.log.map((l) => `<tr><td>${when(l.at)}</td><td>${esc(l.kind)}</td><td class="wrap">${esc((l.detail || '').slice(0, 160))}</td></tr>`), 'Nothing logged yet.')}`;
  },
  async journal() {
    $('lab-body').innerHTML = `<h2>TRADE JOURNAL</h2><div class="controls"><input id="j-q" placeholder="search: contract, reason, state, date..." size="32">
      <select id="j-agent"><option value="">all agents</option>${ROSTER.map((r) => `<option>${r.id}</option>`).join('')}</select>
      <select id="j-source"><option value="">paper and backtests</option><option value="paper">paper trading</option><option value="backtest">backtests (simulated)</option></select>
      <button id="j-go">SEARCH</button></div><div id="j-out" class="dim">Loading...</div>`;
    const run = async () => {
      const d = await get(`/api/journal?q=${encodeURIComponent($('j-q').value)}&agent=${$('j-agent').value}&source=${$('j-source').value}`);
      $('j-out').innerHTML = `<div class="note">${d.total.toLocaleString()} trade(s)${d.total > d.rows.length ? ', showing the first ' + d.rows.length : ''}.</div>` +
        table(['In', 'Out', 'Agent', 'What', 'Side', 'State', 'Result', 'Return', 'Why in / why out', 'Made by'], d.rows.map((t) => `<tr><td>${t.entry_day}</td><td>${t.exit_day}</td><td><b>${t.agent}</b></td>
          <td>${esc(t.instrument)}</td><td>${t.direction === 'SHORT' ? 'short / put' : 'long / call'}</td><td>${esc(t.state || '-')}</td><td class="${cls(t.pnl)}">${money(t.pnl)}</td><td class="${cls(t.ret)}">${pct(t.ret)}</td>
          <td class="wrap">${esc(t.entry_reason || '')}${t.entry_reason ? ' / ' : ''}${esc(t.exit_reason || '')}</td><td>${origin(t.simulated, t.source)} <span class="dim">${t.simulated ? esc(t.source) : ''}</span></td></tr>`), 'No trades match.');
    };
    $('j-go').addEventListener('click', run);
    $('j-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    for (const id of ['j-agent', 'j-source']) $(id).addEventListener('change', run);
    await run();
    return null;
  },
  async replay() { buildReplay(); return null; },
};

// ---------------------------------------------------------------- backtest lab: historical replay

const replay = { data: null, i: 0, speed: 1, playing: false, timer: null };
function buildReplay() {
  $('lab-body').innerHTML = `<h2>BACKTEST LAB: HISTORICAL REPLAY ${tag('SIMULATED', 'gold')}</h2>
    <div class="note">Watch an agent decide day by day through history. It runs the same decision code as paper trading. Speed only changes how fast you watch; the result is fixed.
      Available years: 2006 to 2019 (Agent Era: 2016 to 2019). Later years are held back for testing.</div>
    <div class="controls"><select id="r-agent">${ROSTER.map((r) => `<option value="${r.id}">${r.name} (${r.symbol})</option>`).join('')}</select>
      <input id="r-start" type="date" value="2008-01-02" min="2006-01-03" max="2019-12-31"><input id="r-end" type="date" value="2009-12-31" min="2006-01-03" max="2019-12-31">
      <button id="r-load">LOAD</button></div>
    <div class="controls"><button id="r-play">PLAY</button><button id="r-pause">PAUSE</button><button id="r-restart">RESTART</button>
      ${[['1', '1x'], ['10', '10x'], ['100', '100x'], ['0', 'MAX']].map(([v, l]) => `<button data-speed="${v}" class="${v === String(replay.speed) ? 'active' : ''}">${l}</button>`).join('')}</div>
    <div id="r-now" class="dim">Choose an agent and dates, then LOAD.</div>
    <canvas id="r-chart" width="820" height="260"></canvas><div id="r-trades"></div>`;
  $('r-load').addEventListener('click', loadReplay);
  $('r-play').addEventListener('click', () => playReplay(true));
  $('r-pause').addEventListener('click', () => playReplay(false));
  $('r-restart').addEventListener('click', () => { replay.i = 0; drawReplay(); playReplay(true); });
  for (const b of $('lab-body').querySelectorAll('[data-speed]')) b.addEventListener('click', () => {
    replay.speed = Number(b.dataset.speed);
    for (const o of $('lab-body').querySelectorAll('[data-speed]')) o.classList.toggle('active', o === b);
    if (replay.playing) playReplay(true);
  });
  $('r-agent').addEventListener('change', () => { if ($('r-agent').value === 'Era' && $('r-start').value < '2016-01-04') { $('r-start').value = '2018-01-02'; $('r-end').value = '2018-12-31'; } });
  if (replay.data) drawReplay();
}
async function loadReplay() {
  playReplay(false);
  $('r-now').textContent = 'Running the engine over those dates...';
  try {
    replay.data = await get(`/api/replay?agent=${$('r-agent').value}&start=${$('r-start').value}&end=${$('r-end').value}`);
    replay.i = 0;
    drawReplay();
    playReplay(true);
  } catch (err) { replay.data = null; $('r-now').innerHTML = `<span class="down">${esc(err.message)}</span>`; }
}
function playReplay(on) {
  clearInterval(replay.timer);
  replay.playing = on && !!replay.data;
  if (!replay.playing) return;
  const step = () => {                                             // 1x is one trading day a second
    const n = replay.data.days.length;
    replay.i = replay.speed === 0 ? n - 1 : Math.min(n - 1, replay.i + (replay.speed >= 100 ? 5 : 1));
    drawReplay();
    if (replay.i >= n - 1) playReplay(false);
  };
  replay.timer = setInterval(step, replay.speed === 0 ? 10 : replay.speed >= 100 ? 50 : 1000 / replay.speed);
}
function drawReplay() {
  const d = replay.data, c = $('r-chart');
  if (!d || !c || section !== 'replay') return;
  const days = d.days, now = days[replay.i] || days[0];
  if (!now) { $('r-now').textContent = 'No trading days in that range.'; return; }
  const worst = Math.max(...days.slice(0, replay.i + 1).map((x) => x.drawdown));
  $('r-now').innerHTML = `<div class="tiles"><div><b>${now.day}</b>historical date (day ${replay.i + 1} of ${days.length})</div>
    <div><b>${esc(now.regime || '-')}</b>market regime</div><div><b>${esc(now.action)}</b>decision</div>
    <div><b class="${cls(now.equity - 20000)}">${usd(now.equity)}</b>simulated account (from $20,000)</div>
    <div><b class="${cls(now.realized)}">${money(now.realized)}</b>realized</div><div><b class="${cls(now.unrealized)}">${money(now.unrealized)}</b>unrealized</div>
    <div><b>${pct(now.drawdown)}</b>drawdown now (worst so far ${pct(worst)})</div><div><b>${esc(d.version)}</b>strategy version</div>
    <div><b>${esc(now.position || 'flat')}</b>position</div></div>
    <div class="note">${esc(now.reason || '')}</div><div class="note">${esc(d.note)}</div>`;
  const g = c.getContext('2d'), w = c.width, h = c.height, vals = days.map((x) => x.equity);
  const lo = Math.min(...vals, 20000), hi = Math.max(...vals, 20000), y = (v) => h - 14 - ((v - lo) / (hi - lo || 1)) * (h - 28), x = (k) => 8 + (k / Math.max(days.length - 1, 1)) * (w - 16);
  g.fillStyle = '#10100f'; g.fillRect(0, 0, w, h);
  g.strokeStyle = '#444444'; g.beginPath(); g.moveTo(0, y(20000)); g.lineTo(w, y(20000)); g.stroke();
  g.strokeStyle = '#55ffff'; g.lineWidth = 2; g.beginPath();
  for (let k = 0; k <= replay.i; k++) k ? g.lineTo(x(k), y(vals[k])) : g.moveTo(x(k), y(vals[k]));
  g.stroke(); g.lineWidth = 1;
  g.fillStyle = '#ffaa00'; g.fillRect(x(replay.i) - 2, y(vals[replay.i]) - 2, 5, 5);
  const seen = d.trades.filter((t) => t.exit_day <= now.day).reverse();
  $('r-trades').innerHTML = `<h3>TRADES SO FAR: ${seen.length} (on the fund itself, not options)</h3>` + table(['In', 'Out', 'Side', 'State', 'Result', 'Why it ended'],
    seen.slice(0, 40).map((t) => `<tr><td>${t.entry_day}</td><td>${t.exit_day}</td><td>${t.direction}</td><td>${esc(t.state || '-')}</td><td class="${cls(t.pnl)}">${money(t.pnl)} (${pct(t.ret)})</td>
      <td class="wrap">${esc(t.exit_reason || '')}</td></tr>`), 'No trades yet.');
}

// ---------------------------------------------------------------- events

function standbyState(note) {                          // what the city shows when the dashboard server is not running
  const flat = { day: null, active: null, top: null, probs: {}, next_probs: {}, size: 0, reason: note, frozen: false, expected_days: null };
  return { type: 'state', standby: true, offline: true, live: false, mode: 'OFFLINE', ts: null, last_close: null,
    vault: { equity: null, day_pnl: null, total_pnl: null, trades: 0, win_rate: null, halted: false, halt_reason: '', broker: '', note },
    bots: ROSTER.map((r) => ({ id: r.id, name: r.name, symbol: r.symbol, focus: r.focus, theme: r.theme, bot: '', status: 'NOT TRADING', status_note: note,
      state: 'STANDBY', regime: flat, strategy: { version: '-', status: 'unknown' }, signal: { action: null, direction: null, reason: '', day: null },
      position: null, day_pnl: 0, total_pnl: 0, trades: 0, win_rate: null, drawdown: 0 })) };
}

function onMessage(msg) {
  if (msg.type !== 'state') return;
  const first = !state;
  syncTowers(msg.bots);
  state = msg;
  updateSigns();
  updateHud();
  if (selected) renderPanel();
  if (inside) drawMonitors();
  if (section === 'overview') renderSection();
  if (first) {
    const hash = location.hash.slice(1);
    if (hash.startsWith('tower=') && towers.has(hash.slice(6))) enterTower(hash.slice(6));
    else if (SECTIONS.some(([k]) => k === hash)) openSection(hash);
  }
}

let ws;
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => { onMessage(standbyState('The dashboard server is not running, so nothing live can be shown.')); setTimeout(connect, 3000); };
}
onMessage(standbyState('Connecting...'));
connect();

// click a tower to inspect its agent
const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
let downAt = null;
canvas.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; controls.autoRotate = false; });
canvas.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return;   // was a drag
  pointer.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects([...towers.values()].map((t) => t.group), true)
    .find((h) => h.object !== undefined && !h.object.material.transparent);
  if (hit && !inside) enterTower(hit.object.userData.bot);
});

// ---------------------------------------------------------------- render loop

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  composer.setSize(innerWidth, innerHeight);
  for (const cam of [camera, roomCam]) { cam.aspect = innerWidth / innerHeight; cam.updateProjectionMatrix(); }
}
addEventListener('resize', resize);
resize();

const v3 = new THREE.Vector3();
function place(el, x, y, z) {
  v3.set(x, y, z).project(camera);
  el.style.display = v3.z > 1 ? 'none' : '';
  el.style.left = ((v3.x * 0.5 + 0.5) * innerWidth).toFixed(1) + 'px';
  el.style.top = ((-v3.y * 0.5 + 0.5) * innerHeight).toFixed(1) + 'px';
}

const clock = new THREE.Clock();
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1), time = clock.elapsedTime;
  controls.update();
  scene.background.lerp(night ? NIGHT : state?.vault.halted ? SKY_LOCKED : SKY, 0.05);
  scene.fog.color.copy(scene.background);
  ambient.intensity += ((night ? 0.75 : 1.5) - ambient.intensity) * 0.05;
  sunLight.intensity += ((night ? 0.35 : 1.7) - sunLight.intensity) * 0.05;
  const down = state && (state.vault.day_pnl ?? 0) < 0;
  for (const m of vault.userData.dome.material) m.color.setHex(down ? 0xffd0c0 : 0xffffff);
  for (const cloud of clouds) { cloud.position.x += dt * 1.2; if (cloud.position.x > 280) cloud.position.x = -280; cloud.material.opacity = night ? 0.25 : 0.88; }
  for (const t of towers.values()) {
    t.tick(time, dt);
    animateTower(t, time, dt);
    const p = t.group.position;
    place(t.sign, p.x, t.top + 5 + t.lift, p.z);
  }
  place(vaultSign, vault.position.x, 19, vault.position.z);
  if (inside) animateRoom(time, dt);
  composer.render();
  requestAnimationFrame(frame);
}
frame();
