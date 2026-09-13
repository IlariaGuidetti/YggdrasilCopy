(function () {
    'use strict';

    function DermatologyAnnotator(opts) {
        this.imageEl = opts.imageEl;
        this.wrapEl = opts.wrapEl;
        this.outerEl = opts.outerEl;
        this.toolbarEl = opts.toolbarEl;
        this.toggleBtn = opts.toggleBtn;
        this.regionListEl = opts.regionListEl;
        this.shapesListEl = opts.shapesListEl;
        this.brushSizeInput = opts.brushSizeInput;
        this.brushSizeLabel = opts.brushSizeLabel;
        this.polygonHintEl = opts.polygonHintEl;
        this.quadrantSelectEl = opts.quadrantSelectEl;
        this.quadrantAdminListEl = opts.quadrantAdminListEl;
        this.addRegionBtnEl = opts.addRegionBtnEl;
        this.addQuadrantBtnEl = opts.addQuadrantBtnEl;
        this.deleteQuadrantBtnEl = opts.deleteQuadrantBtnEl;

        this.patientId = opts.patientId;
        this.fileRegistryId = opts.fileRegistryId;
        this.isAdmin = !!opts.isAdmin;
        this.csrfToken = opts.csrfToken;
        this.apiBase = opts.apiBase; // e.g. '/dermatology'
        this.revision = 0;

        this.annotationMode = false;
        this.currentTool = 'brush';
        this.brushSize = 8;

        this.regionTypes = [];   // [{id, name, color}]
        this.quadrantTypes = []; // [{id, name, color}]
        this.activeRegionId = null;
        this.shapes = [];        // [{id, dbId, type, regionId, konvaNode}]
        this._selectedShapeId = null;

        this._polyPoints = [];
        this._polyLine = null;
        this._polyGuide = null;
        this._polyDots = [];
        this._drawing = false;
        this._currentLine = null;

        this._panX = 0;
        this._panY = 0;
        this._scale = 1;
        this._isPanning = false;

        this._init();
    }

    DermatologyAnnotator.prototype._init = function () {
        var self = this;

        this.stage = new Konva.Stage({
            container: this.wrapEl,
            width: this.imageEl.naturalWidth,
            height: this.imageEl.naturalHeight,
        });
        this.bgLayer = new Konva.Layer();
        this.annLayer = new Konva.Layer();
        this.cursorLayer = new Konva.Layer();
        this.stage.add(this.bgLayer);
        this.stage.add(this.annLayer);
        this.stage.add(this.cursorLayer);

        var konvaImg = new Konva.Image({
            image: this.imageEl,
            width: this.imageEl.naturalWidth,
            height: this.imageEl.naturalHeight,
        });
        this.bgLayer.add(konvaImg);
        this.bgLayer.draw();

        this._cursorCircle = new Konva.Circle({
            radius: this.brushSize / 2,
            stroke: '#fff', strokeWidth: 1, dash: [4, 2],
            listening: false, visible: false,
        });
        this.cursorLayer.add(this._cursorCircle);

        this._syncStageSize();
        window.addEventListener('resize', function () { self._syncStageSize(); });

        this._bindToggle();
        this._bindToolbar();

        this._loadRegionTypes().then(function () {
            self._loadQuadrantTypes().then(function () {
                self._loadAnnotations();
            });
        });
    };

    DermatologyAnnotator.prototype._syncStageSize = function () {
        var containerWidth = this.outerEl.clientWidth || 800;
        var maxHeight = window.innerHeight * 0.65;
        var natW = this.imageEl.naturalWidth || 1;
        var natH = this.imageEl.naturalHeight || 1;

        var scaleByWidth = containerWidth / natW;
        var scaleByHeight = maxHeight / natH;
        var fitScale = Math.min(scaleByWidth, scaleByHeight);
        var scale = fitScale * this._scale;

        this._fitScale = fitScale;
        var w = Math.round(natW * scale);
        var h = Math.round(natH * scale);
        this.stage.width(w);
        this.stage.height(h);
        this.stage.scale({ x: scale, y: scale });
        this.stage.position({ x: this._panX, y: this._panY });
        this.wrapEl.style.width = w + 'px';
        this.wrapEl.style.height = h + 'px';
        this.stage.draw();
    };

    DermatologyAnnotator.prototype._pointerPos = function () {
        var pos = this.stage.getPointerPosition();
        if (!pos) return null;
        var transform = this.stage.getAbsoluteTransform().copy().invert();
        return transform.point(pos);
    };

    // ─── mode / toolbar ─────────────────────────────────────────────────

    DermatologyAnnotator.prototype._bindToggle = function () {
        var self = this;
        this.toggleBtn.addEventListener('click', function () {
            if (self.annotationMode) self._exitAnnotationMode();
            else self._enterAnnotationMode();
        });
    };

    DermatologyAnnotator.prototype._enterAnnotationMode = function () {
        this.annotationMode = true;
        this.outerEl.style.outline = '3px solid #e74c3c';
        this.toolbarEl.classList.remove('d-none');
        this._bindDrawing();
        this.toggleBtn.textContent = 'Exit Annotation Mode';
        this.toggleBtn.classList.remove('btn-outline-warning');
        this.toggleBtn.classList.add('btn-warning');
    };

    DermatologyAnnotator.prototype._exitAnnotationMode = function () {
        this.annotationMode = false;
        this.outerEl.style.outline = '';
        this._cancelPolygon();
        this._unbindDrawing();
        this.toolbarEl.classList.add('d-none');
        this.toggleBtn.textContent = 'Enter Annotation Mode';
        this.toggleBtn.classList.remove('btn-warning');
        this.toggleBtn.classList.add('btn-outline-warning');
    };

    DermatologyAnnotator.prototype._bindToolbar = function () {
        var self = this;
        this.toolbarEl.querySelectorAll('[data-tool]').forEach(function (btn) {
            btn.addEventListener('click', function () { self._setTool(btn.getAttribute('data-tool')); });
        });
        if (this.brushSizeInput) {
            this.brushSizeInput.addEventListener('input', function () {
                self.brushSize = parseInt(this.value, 10);
                if (self.brushSizeLabel) self.brushSizeLabel.textContent = self.brushSize;
                if (self._cursorCircle) self._cursorCircle.radius(self.brushSize / 2);
            });
        }
        if (this.addRegionBtnEl) {
            this.addRegionBtnEl.addEventListener('click', function () {
                if (!self.isAdmin) return;
                var name = prompt('New region type name:');
                if (!name) return;
                self._createRegionType(name);
            });
        }
        if (this.addQuadrantBtnEl) {
            this.addQuadrantBtnEl.addEventListener('click', function () {
                if (!self.isAdmin) return;
                var name = prompt('New body region name:');
                if (!name) return;
                self._createQuadrantType(name);
            });
        }
        if (this.deleteQuadrantBtnEl) {
            this.deleteQuadrantBtnEl.addEventListener('click', function () {
                if (!self.isAdmin) return;
                self._deleteQuadrantType();
            });
        }
        if (this.quadrantSelectEl) {
            this.quadrantSelectEl.addEventListener('change', function () {
                self._saveQuadrantMarker(self.quadrantSelectEl.value);
            });
        }
    };

    DermatologyAnnotator.prototype._setTool = function (tool) {
        this.currentTool = tool;
        this._cancelPolygon();
        this.toolbarEl.querySelectorAll('[data-tool]').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-tool') === tool);
        });
        this.outerEl.style.cursor = (tool === 'pan') ? 'grab' : (tool === 'eraser' ? 'cell' : 'crosshair');
    };

    // ─── drawing ─────────────────────────────────────────────────────────

    DermatologyAnnotator.prototype._bindDrawing = function () {
        var self = this;

        this._onStageDown = function () {
            if (!self.annotationMode || self.currentTool === 'pan') return;
            var pos = self._pointerPos();
            if (!pos) return;
            if (self.currentTool === 'polygon') { self._polyAddVertex(pos); return; }
            self._startDrawing(pos);
        };
        this.stage.on('mousedown touchstart', this._onStageDown);

        this._onStageMove = function () {
            var pos = self._pointerPos();
            if (!pos) return;
            if (self._cursorCircle) {
                var isEraser = self.currentTool === 'eraser';
                self._cursorCircle.visible(isEraser);
                if (isEraser) { self._cursorCircle.position(pos); self.cursorLayer.draw(); }
            }
            if (self.currentTool === 'polygon') { self._polyUpdateGuide(pos); return; }
            self._continueDrawing(pos);
        };
        this.stage.on('mousemove touchmove', this._onStageMove);

        this._onStageUp = function () { self._finishDrawing(); };
        window.addEventListener('mouseup', this._onStageUp);

        this._onDblClick = function () { if (self.currentTool === 'polygon') self._polyClose(); };
        this.stage.on('dblclick dbltap', this._onDblClick);

        document.addEventListener('keydown', this._onKeyDown = function (e) {
            if (!self.annotationMode) return;
            if (e.key === 'Enter' && self.currentTool === 'polygon') self._polyClose();
            if (e.key === 'Escape') self._cancelPolygon();
        });
    };

    DermatologyAnnotator.prototype._unbindDrawing = function () {
        if (this._onStageDown) this.stage.off('mousedown touchstart', this._onStageDown);
        if (this._onStageMove) this.stage.off('mousemove touchmove', this._onStageMove);
        if (this._onStageUp) window.removeEventListener('mouseup', this._onStageUp);
        if (this._onDblClick) this.stage.off('dblclick dbltap', this._onDblClick);
        if (this._onKeyDown) document.removeEventListener('keydown', this._onKeyDown);
        if (this._cursorCircle) { this._cursorCircle.visible(false); this.cursorLayer.draw(); }
    };

    DermatologyAnnotator.prototype._activeRegion = function () {
        var self = this;
        return this.regionTypes.find(function (r) { return r.id === self.activeRegionId; }) || this.regionTypes[0] || null;
    };

    DermatologyAnnotator.prototype._startDrawing = function (pos) {
        var region = this._activeRegion();
        if (!region) { alert('Create a region type first.'); return; }
        this._drawing = true;
        var isEraser = this.currentTool === 'eraser';
        var line = new Konva.Line({
            stroke: isEraser ? 'rgba(0,0,0,1)' : region.color,
            strokeWidth: this.brushSize,
            globalCompositeOperation: isEraser ? 'destination-out' : 'source-over',
            lineCap: 'round', lineJoin: 'round',
            points: [pos.x, pos.y, pos.x, pos.y],
        });
        this.annLayer.add(line);
        this._currentLine = line;
    };

    DermatologyAnnotator.prototype._continueDrawing = function (pos) {
        if (!this._drawing || !this._currentLine) return;
        var pts = this._currentLine.points();
        pts.push(pos.x, pos.y);
        this._currentLine.points(pts);
        this.annLayer.batchDraw();
    };

    DermatologyAnnotator.prototype._finishDrawing = function () {
        if (this._drawing && this._currentLine) {
            this._registerShape(this.currentTool, this._currentLine);
        }
        this._drawing = false;
        this._currentLine = null;
    };

    DermatologyAnnotator.prototype._polyAddVertex = function (pos) {
        var region = this._activeRegion();
        if (!region) { alert('Create a region type first.'); return; }
        this._polyPoints.push(pos.x, pos.y);
        if (this._polyPoints.length === 2 && this.polygonHintEl) this.polygonHintEl.classList.remove('d-none');
        if (!this._polyLine) {
            this._polyLine = new Konva.Line({ points: this._polyPoints.slice(), stroke: region.color, strokeWidth: 2, dash: [6, 3] });
            this.annLayer.add(this._polyLine);
            this._polyGuide = new Konva.Line({ points: [pos.x, pos.y, pos.x, pos.y], stroke: region.color, strokeWidth: 1, dash: [4, 2], opacity: 0.5 });
            this.annLayer.add(this._polyGuide);
        } else {
            this._polyLine.points(this._polyPoints.slice());
        }
        var dot = new Konva.Circle({ x: pos.x, y: pos.y, radius: 4, fill: region.color });
        this.annLayer.add(dot);
        this._polyDots.push(dot);
        this.annLayer.draw();
    };

    DermatologyAnnotator.prototype._polyUpdateGuide = function (pos) {
        if (!this._polyGuide || this._polyPoints.length < 2) return;
        var last = this._polyPoints.slice(-2);
        this._polyGuide.points([last[0], last[1], pos.x, pos.y]);
        this.annLayer.draw();
    };

    DermatologyAnnotator.prototype._polyClose = function () {
        if (this._polyPoints.length < 6) { this._cancelPolygon(); return; }
        var region = this._activeRegion();
        if (!region) { this._cancelPolygon(); return; }
        if (this._polyLine) this._polyLine.destroy();
        if (this._polyGuide) this._polyGuide.destroy();
        this._polyDots.forEach(function (d) { d.destroy(); });
        var filled = new Konva.Line({
            points: this._polyPoints.slice(),
            fill: region.color + '55', stroke: region.color, strokeWidth: 2, closed: true,
        });
        this.annLayer.add(filled);
        this.annLayer.draw();
        this._registerShape('polygon', filled);
        this._resetPolyState();
    };

    DermatologyAnnotator.prototype._cancelPolygon = function () {
        if (this._polyLine) this._polyLine.destroy();
        if (this._polyGuide) this._polyGuide.destroy();
        this._polyDots.forEach(function (d) { d.destroy(); });
        this.annLayer.draw();
        this._resetPolyState();
    };

    DermatologyAnnotator.prototype._resetPolyState = function () {
        this._polyPoints = [];
        this._polyLine = null;
        this._polyGuide = null;
        this._polyDots = [];
        if (this.polygonHintEl) this.polygonHintEl.classList.add('d-none');
    };

    // ─── shapes: register / list / delete ──────────────────────────────

    DermatologyAnnotator.prototype._registerShape = function (type, konvaNode) {
        var region = this._activeRegion();
        var id = 'shape-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        var shape = { id: id, dbId: null, type: type, regionId: region ? region.id : null, konvaNode: konvaNode };
        this.shapes.push(shape);
        this._saveAllShapes();
        this._renderShapesList();
        return shape;
    };

    DermatologyAnnotator.prototype._renderShapesList = function () {
        var self = this;
        if (!this.shapesListEl) return;
        this.shapesListEl.innerHTML = '';
        if (!this.shapes.length) {
            var empty = document.createElement('li');
            empty.className = 'list-group-item text-muted small py-1 px-2';
            empty.textContent = 'No annotations yet.';
            this.shapesListEl.appendChild(empty);
            return;
        }
        this.shapes.slice().reverse().forEach(function (s) {
            var region = self.regionTypes.find(function (r) { return r.id === s.regionId; });
            var li = document.createElement('li');
            li.className = 'list-group-item d-flex align-items-center gap-2 py-1 px-2';
            var dot = document.createElement('span');
            dot.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:50%;flex-shrink:0;background:' + (region ? region.color : '#888');
            li.appendChild(dot);
            var label = document.createElement('span');
            label.className = 'flex-grow-1 small text-truncate';
            label.textContent = (region ? region.name : '?') + ' \u2022 ' + s.type;
            li.appendChild(label);
            var delBtn = document.createElement('button');
            delBtn.className = 'btn btn-sm btn-outline-danger py-0 px-2';
            delBtn.innerHTML = '<i class="fas fa-trash-alt"></i>';
            delBtn.addEventListener('click', function () { self._deleteShape(s.id); });
            li.appendChild(delBtn);
            self.shapesListEl.appendChild(li);
        });
    };

    DermatologyAnnotator.prototype._deleteShape = function (id) {
        var idx = this.shapes.findIndex(function (s) { return s.id === id; });
        if (idx === -1) return;
        var s = this.shapes[idx];
        s.konvaNode.destroy();
        this.annLayer.draw();
        this.shapes.splice(idx, 1);
        this._renderShapesList();
        this._saveAllShapes();
    };

    // ─── region types panel ─────────────────────────────────────────────

    var REGION_COLOR_PALETTE = [
        '#3498db', '#e74c3c', '#2ecc71', '#f39c12',
        '#9b59b6', '#1abc9c', '#e67e22', '#34495e',
        '#16a085', '#c0392b', '#8e44ad', '#2980b9',
        '#27ae60', '#d35400', '#7f8c8d', '#2c3e50'
    ];

    DermatologyAnnotator.prototype._renderRegionList = function () {
        var self = this;
        if (!this.regionListEl) return;
        this.regionListEl.innerHTML = '';
        this.regionTypes.forEach(function (r) {
            var li = document.createElement('li');
            li.className = 'region-chip' + (r.id === self.activeRegionId ? ' is-active' : '');
            li.style.cssText = 'display:inline-flex;align-items:center;gap:0.35rem;padding:0.2rem 0.6rem;border-radius:999px;border:1px solid #ddd;cursor:pointer;margin:2px;';
            if (r.id === self.activeRegionId) li.style.borderColor = r.color;
            var dot = document.createElement('span');
            dot.style.cssText = 'width:10px;height:10px;border-radius:50%;background:' + r.color + ';flex-shrink:0;';
            li.appendChild(dot);
            var name = document.createElement('span');
            name.className = 'small';
            name.textContent = r.name;
            li.appendChild(name);
            li.addEventListener('click', function () {
                self.activeRegionId = r.id;
                self._renderRegionList();
            });
            if (self.isAdmin) {
                var delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.innerHTML = '&times;';
                delBtn.title = 'Delete region type';
                delBtn.style.cssText = 'border:none;background:transparent;color:#999;line-height:1;padding:0 0 0 2px;font-size:0.9rem;';
                delBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    if (!confirm('Delete region type "' + r.name + '"? This will also delete all annotations using it.')) return;
                    self._deleteRegionType(r.id);
                });
                li.appendChild(delBtn);
            }
            self.regionListEl.appendChild(li);
        });
    };

    DermatologyAnnotator.prototype._deleteRegionType = function (id) {
        var self = this;
        fetch(this.apiBase + '/api/region-types/' + id + '/', {
            method: 'DELETE', headers: { 'X-CSRFToken': this.csrfToken },
        }).then(function (resp) {
            if (!resp.ok) throw new Error('Delete failed');
            self.regionTypes = self.regionTypes.filter(function (r) { return r.id !== id; });
            self.shapes = self.shapes.filter(function (s) {
                if (s.regionId === id) { s.konvaNode.destroy(); return false; }
                return true;
            });
            self.annLayer.draw();
            if (self.activeRegionId === id) self.activeRegionId = self.regionTypes.length ? self.regionTypes[0].id : null;
            self._renderRegionList();
            self._renderShapesList();
            self._saveAllShapes();
        }).catch(function () {
            alert('Could not delete region type.');
        });
    };

    // ─── API calls: types (still on the legacy dermatology API) ───────

    DermatologyAnnotator.prototype._headers = function () {
        return { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken };
    };

    DermatologyAnnotator.prototype._loadRegionTypes = function () {
        var self = this;
        return fetch(this.apiBase + '/api/region-types/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                self.regionTypes = data.types || [];
                if (!self.activeRegionId && self.regionTypes.length) self.activeRegionId = self.regionTypes[0].id;
                self._renderRegionList();
            });
    };

    DermatologyAnnotator.prototype._createRegionType = function (name) {
        var self = this;
        var color = REGION_COLOR_PALETTE[this.regionTypes.length % REGION_COLOR_PALETTE.length];
        fetch(this.apiBase + '/api/region-types/', {
            method: 'POST', headers: this._headers(), body: JSON.stringify({ name: name, color: color }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.id) {
                self.regionTypes.push(data);
                self.activeRegionId = data.id;
                self._renderRegionList();
            }
        });
    };

    DermatologyAnnotator.prototype._loadQuadrantTypes = function () {
        var self = this;
        return fetch(this.apiBase + '/api/quadrant-types/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                self.quadrantTypes = data.types || [];
                self._renderQuadrantSelect();
            });
    };

    DermatologyAnnotator.prototype._createQuadrantType = function (name) {
        var self = this;
        fetch(this.apiBase + '/api/quadrant-types/', {
            method: 'POST', headers: this._headers(), body: JSON.stringify({ name: name }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.id) {
                self.quadrantTypes.push(data);
                self._renderQuadrantSelect();
            }
        });
    };

    DermatologyAnnotator.prototype._renderQuadrantSelect = function () {
        var self = this;
        if (!this.quadrantSelectEl) return;
        var current = this.quadrantSelectEl.value;
        this.quadrantSelectEl.innerHTML = '<option value="">— Body region —</option>';
        this.quadrantTypes.forEach(function (q) {
            var opt = document.createElement('option');
            opt.value = q.id;
            opt.textContent = q.name;
            self.quadrantSelectEl.appendChild(opt);
        });
        if (current) this.quadrantSelectEl.value = current;
    };

    DermatologyAnnotator.prototype._deleteQuadrantType = function () {
        var self = this;
        var id = this.quadrantSelectEl ? this.quadrantSelectEl.value : null;
        if (!id) { alert('Select a body region first.'); return; }
        var q = this.quadrantTypes.find(function (t) { return String(t.id) === String(id); });
        if (!q) return;
        if (!confirm('Delete body region "' + q.name + '"?')) return;

        fetch(this.apiBase + '/api/quadrant-types/' + id + '/', {
            method: 'DELETE', headers: { 'X-CSRFToken': this.csrfToken },
        }).then(function (resp) {
            if (resp.status === 204) {
                self.quadrantTypes = self.quadrantTypes.filter(function (t) { return String(t.id) !== String(id); });
                self._renderQuadrantSelect();
                return;
            }
            return resp.json().then(function (data) {
                if (data.error && data.error.includes('replacement_id')) {
                    var replacementName = prompt('This body region is in use. Type the exact name of another body region to reassign markers to:');
                    if (!replacementName) return;
                    var replacement = self.quadrantTypes.find(function (t) { return t.name === replacementName; });
                    if (!replacement) { alert('No body region found with that name.'); return; }
                    return fetch(self.apiBase + '/api/quadrant-types/' + id + '/', {
                        method: 'DELETE', headers: self._headers(),
                        body: JSON.stringify({ replacement_id: replacement.id }),
                    }).then(function (resp2) {
                        if (resp2.status === 204) {
                            self.quadrantTypes = self.quadrantTypes.filter(function (t) { return String(t.id) !== String(id); });
                            self._renderQuadrantSelect();
                        } else {
                            alert('Could not delete body region.');
                        }
                    });
                }
                alert('Could not delete body region.');
            });
        });
    };

    DermatologyAnnotator.prototype._quadrantIdByName = function (name) {
        var found = this.quadrantTypes.find(function (q) { return q.name === name; });
        return found ? found.id : null;
    };

    // ─── API calls: annotations (new, shared annotations backend) ──────

    DermatologyAnnotator.prototype._loadAnnotations = function () {
        var self = this;
        fetch(this.apiBase + '/api/patients/' + this.patientId + '/dermatology-annotations/state/')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                self.revision = data.revision || 0;
                (data.shapes || []).forEach(function (shape) {
                    self._drawExistingShape(shape);
                });
                if (data.quadrantName && self.quadrantSelectEl) {
                    self.quadrantSelectEl.value = self._quadrantIdByName(data.quadrantName) || '';
                }
                self._renderShapesList();
            })
            .catch(function () { /* new patient, no annotations yet */ });
    };

    DermatologyAnnotator.prototype._drawExistingShape = function (shape) {
        var region = this.regionTypes.find(function (r) { return r.name === shape.regionName; });
        var color = region ? region.color : '#888';
        var node;
        if (shape.tool === 'polygon') {
            node = new Konva.Line({ points: shape.points, fill: color + '55', stroke: color, strokeWidth: shape.strokeWidth || 2, closed: true });
        } else {
            node = new Konva.Line({
                points: shape.points, stroke: color, strokeWidth: shape.strokeWidth || 8,
                lineCap: 'round', lineJoin: 'round',
                globalCompositeOperation: shape.tool === 'eraser' ? 'destination-out' : 'source-over',
            });
        }
        this.annLayer.add(node);
        this.annLayer.draw();
        this.shapes.push({ id: 'shape-' + Date.now() + Math.random(), dbId: null, type: shape.tool, regionId: region ? region.id : null, konvaNode: node });
    };

    DermatologyAnnotator.prototype._saveAllShapes = function () {
        var self = this;
        var payloadShapes = this.shapes.map(function (s) {
            var region = self.regionTypes.find(function (r) { return r.id === s.regionId; });
            return {
                tool: s.type,
                points: s.konvaNode.points(),
                strokeWidth: s.konvaNode.strokeWidth(),
                regionName: region ? region.name : null,
            };
        });
        var quadrant = this.quadrantTypes.find(function (q) {
            return self.quadrantSelectEl && String(q.id) === String(self.quadrantSelectEl.value);
        });

        fetch(this.apiBase + '/api/patients/' + this.patientId + '/dermatology-annotations/', {
            method: 'POST',
            headers: this._headers(),
            body: JSON.stringify({
                expectedRevision: this.revision,
                fileId: this.fileRegistryId,
                shapes: payloadShapes,
                quadrantName: quadrant ? quadrant.name : null,
            }),
        })
            .then(function (r) {
                if (!r.ok) {
                    return r.json().then(function (errData) {
                        throw new Error(errData.error || ('HTTP ' + r.status));
                    });
                }
                return r.json();
            })
            .then(function (data) {
                self.revision = data.revision;
            })
            .catch(function (err) {
                alert('Could not save annotations: ' + err.message);
            });
    };

    DermatologyAnnotator.prototype._saveQuadrantMarker = function () {
        this._saveAllShapes();
    };

    window.DermatologyAnnotator = DermatologyAnnotator;
})();