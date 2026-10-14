/* MineModelingPro — the landing page hero.
   Renders a real MMP model (site/hero.json): real collars, real desurveyed traces,
   real grade-coloured intervals and a real inverse-distance block model, trimmed to
   something a landing page can load. Drag to orbit; it turns on its own otherwise. */
(function () {
  const host = document.getElementById("hero3d");
  if (!host || typeof THREE === "undefined") return;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x14171c);
  const cam = new THREE.PerspectiveCamera(42, 1, 1, 60000);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  host.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff, 0.95));
  const key = new THREE.DirectionalLight(0xffffff, 0.55);
  key.position.set(1, 1.4, 0.9);
  scene.add(key);

  const un64 = (s, T) => {
    const bin = atob(s), u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return new T(u.buffer);
  };
  const RAD = Math.PI / 180;

  function binColor(g, bins, colors) {
    let k = 0;
    for (let i = 0; i < bins.length; i++) if (g >= bins[i]) k = i;
    return new THREE.Color(colors[Math.min(k, colors.length - 1)] || "#D71920");
  }

  let group = null, spin = true, drag = null, yaw = 0.7, pitch = 0.33, dist = 1, target = new THREE.Vector3();

  fetch("hero.json").then(r => r.json()).then(M => {
    group = new THREE.Group();
    const holes = M.holes, segs = M.segments, B = M.blocks;

    // centre on the drilling
    let cx = 0, cy = 0, cz = 0;
    holes.forEach(h => { cx += h[0]; cy += h[1]; cz += h[2]; });
    cx /= holes.length; cy /= holes.length; cz /= holes.length;
    const at = (h, d) => new THREE.Vector3(
      h[0] - cx + d * Math.cos(h[4] * RAD) * Math.sin(h[3] * RAD),
      h[2] - cz + d * Math.sin(h[4] * RAD),
      -(h[1] - cy + d * Math.cos(h[4] * RAD) * Math.cos(h[3] * RAD)));

    /* terrain, as a quiet surface so the holes have a ground to come out of */
    if (M.terrain) {
      const t = M.terrain, z = un64(t.z, Float32Array);
      const geo = new THREE.PlaneGeometry((t.nx - 1) * t.dx, (t.ny - 1) * t.dy, t.nx - 1, t.ny - 1);
      const pos = geo.attributes.position;
      for (let j = 0; j < t.ny; j++) for (let i = 0; i < t.nx; i++) {
        const k = j * t.nx + i;
        pos.setZ(k, z[k] - cz);
      }
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({
        color: 0x242a31, transparent: true, opacity: 0.85, side: THREE.DoubleSide,
      }));
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(t.x0 + (t.nx - 1) * t.dx / 2 - cx, 0, -(t.y0 + (t.ny - 1) * t.dy / 2 - cy));
      group.add(mesh);
    }

    /* every hole, as a faint trace */
    const lp = [];
    holes.forEach(h => { const a = at(h, 0), b = at(h, h[5] || 0); lp.push(a.x, a.y, a.z, b.x, b.y, b.z); });
    const lgeo = new THREE.BufferGeometry();
    lgeo.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
    group.add(new THREE.LineSegments(lgeo, new THREE.LineBasicMaterial({
      color: 0x8c99a8, transparent: true, opacity: 0.33 })));

    /* the assays: a box per interval, oriented down the hole, coloured by grade */
    if (segs.length) {
      const g = new THREE.BoxGeometry(1, 1, 1);
      const im = new THREE.InstancedMesh(g, new THREE.MeshLambertMaterial({}), segs.length);
      const d = new THREE.Object3D(), c = new THREE.Color();
      segs.forEach((s, i) => {
        const h = holes[s[0]]; if (!h) return;
        const a = at(h, s[1]), b = at(h, s[2]);
        d.position.copy(a).add(b).multiplyScalar(0.5);
        d.lookAt(b);
        const len = a.distanceTo(b) || 1;
        d.scale.set(16, 16, len);
        d.updateMatrix();
        im.setMatrixAt(i, d.matrix);
        im.setColorAt(i, binColor(s[3], M.bins, M.colors));
      });
      im.instanceMatrix.needsUpdate = true;
      group.add(im);
    }

    /* the block model */
    if (B && B.n) {
      const ijk = un64(B.ijk, Uint16Array), gg = un64(B.g, Float32Array);
      const g = new THREE.BoxGeometry(B.size[0], B.size[2], B.size[1]);
      const im = new THREE.InstancedMesh(g, new THREE.MeshLambertMaterial({
        transparent: true, opacity: 0.95 }), B.n);
      const d = new THREE.Object3D();
      for (let i = 0; i < B.n; i++) {
        const x = B.origin[0] + ijk[i * 3] * B.size[0], y = B.origin[1] + ijk[i * 3 + 1] * B.size[1],
              z = B.origin[2] + ijk[i * 3 + 2] * B.size[2];
        d.position.set(x - cx, z - cz, -(y - cy));
        d.updateMatrix();
        im.setMatrixAt(i, d.matrix);
        im.setColorAt(i, binColor(gg[i], M.bins, M.colors));
      }
      im.instanceMatrix.needsUpdate = true;
      group.add(im);
    }

    scene.add(group);

    /* frame the deposit, not the landscape around it */
    const box = new THREE.Box3();
    segs.forEach(s => {
      const h = holes[s[0]]; if (!h) return;
      box.expandByPoint(at(h, s[1])); box.expandByPoint(at(h, s[2]));
    });
    if (B && B.n) {
      const ijk = un64(B.ijk, Uint16Array), v = new THREE.Vector3();
      for (let i = 0; i < B.n; i += 7) {
        box.expandByPoint(v.set(B.origin[0] + ijk[i * 3] * B.size[0] - cx,
                                B.origin[2] + ijk[i * 3 + 2] * B.size[2] - cz,
                                -(B.origin[1] + ijk[i * 3 + 1] * B.size[1] - cy)));
      }
    }
    if (box.isEmpty()) box.setFromObject(group);
    const size = box.getSize(new THREE.Vector3());
    target.copy(box.getCenter(new THREE.Vector3()));
    dist = Math.max(size.x, size.y, size.z) * 1.35 + 120;
    resize();

    const cap = document.getElementById("herocap");
    if (cap) {
      cap.innerHTML = `<b>${M.project}</b>${M.region ? " · " + M.region : ""} — `
        + `${M.counts.holes.toLocaleString()} holes, ${M.counts.intervals.toLocaleString()} assay intervals, `
        + `${M.counts.blocks.toLocaleString()} blocks, from its NI 43-101 filings`;
    }
  }).catch(() => { host.style.display = "none"; });

  /* ---- interaction: drag to orbit, wheel to zoom, idle spin ---- */
  function place() {
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    cam.position.set(target.x + dist * cp * Math.sin(yaw), target.y + dist * sp,
                     target.z + dist * cp * Math.cos(yaw));
    cam.lookAt(target);
  }
  function resize() {
    const w = host.clientWidth || 600, h = host.clientHeight || 320;
    renderer.setSize(w, h, false);
    cam.aspect = w / h; cam.updateProjectionMatrix();
    place();
  }
  addEventListener("resize", resize);
  host.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY }; spin = false; host.setPointerCapture(e.pointerId); });
  host.addEventListener("pointermove", e => {
    if (!drag) return;
    yaw -= (e.clientX - drag.x) * 0.006;
    pitch = Math.max(-0.2, Math.min(1.3, pitch + (e.clientY - drag.y) * 0.004));
    drag = { x: e.clientX, y: e.clientY };
    place();
  });
  addEventListener("pointerup", () => { drag = null; });
  host.addEventListener("wheel", e => {
    e.preventDefault(); dist *= e.deltaY > 0 ? 1.08 : 0.93; place();
  }, { passive: false });

  let last = 0;
  (function loop(t) {
    requestAnimationFrame(loop);
    if (spin && group) { yaw += (t - last) * 0.00006; place(); }
    last = t;
    renderer.render(scene, cam);
  })(0);
})();
