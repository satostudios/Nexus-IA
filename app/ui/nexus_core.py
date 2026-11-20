"""The NEXUS Core: a procedural, audio-reactive visual rendered on the GPU.

Two OpenGL 3.3 passes (``assets/shaders``):

1. scene (HDR framebuffer): noisy irregular orb with a stippled particle
   surface, inclined orbital rings, a long edge-on ring, drifting particles;
2. post: RGB chromatic aberration, mip-chain bloom, subtle glitch, scanlines,
   film grain, vignette.

Driven at ~60 FPS by a precise timer; all motion comes from
:class:`CoreDynamics`. When OpenGL 3.3 is unavailable (some VMs / remote
desktops) :class:`PainterCore` draws a simpler QPainter version from the same
parameters so the app still works.
"""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Callable

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QGuiApplication, QOffscreenSurface, QOpenGLContext, QPainter, QPen,
                           QRadialGradient, QSurfaceFormat, QVector2D, QVector3D)
from PySide6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from app import paths
from app.core.state import NexusState
from app.ui.core_dynamics import CoreDynamics

log = logging.getLogger("nexus.core")

GL_TRIANGLES = 0x0004
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_CLAMP_TO_EDGE = 0x812F
GL_LINEAR = 0x2601
GL_LINEAR_MIPMAP_LINEAR = 0x2703
GL_COLOR_BUFFER_BIT = 0x4000
GL_RGBA16F = 0x881A

LevelsSource = Callable[[], dict]


def configure_default_format() -> None:
    """Request a 3.3 core context with vsync. Call before QApplication exists."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setSwapInterval(1)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)


def opengl_available() -> bool:
    """Probe for a usable OpenGL 3.3 core context (needs a QGuiApplication)."""
    try:
        ctx = QOpenGLContext()
        ctx.setFormat(QSurfaceFormat.defaultFormat())
        if not ctx.create():
            return False
        fmt = ctx.format()
        if (fmt.majorVersion(), fmt.minorVersion()) < (3, 3):
            return False
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        ok = ctx.makeCurrent(surface)
        if ok:
            renderer = ctx.functions().glGetString(0x1F01)
            log.info("OpenGL %d.%d: %s", fmt.majorVersion(), fmt.minorVersion(), renderer)
            ctx.doneCurrent()
        return bool(ok)
    except Exception:
        log.exception("OpenGL probe failed")
        return False


class _CoreBase:
    """Shared animation driver for both renderers."""

    def _init_driver(self, levels: LevelsSource) -> None:
        self.dynamics = CoreDynamics()
        self._levels = levels
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = 0.0
        self._fps_frames = 0
        self._fps_since = time.perf_counter()
        self.fps = 0.0
        self.uniforms: dict[str, object] = {}
        self._timer = QTimer(self)  # type: ignore[arg-type]
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)  # type: ignore[attr-defined]
        self._timer.start()

    def set_state(self, state: NexusState) -> None:
        self.dynamics.set_state(state)

    def pulse(self, kind: str) -> None:
        self.dynamics.trigger_pulse(1.0 if kind == "success" else 0.5)

    def _advance(self) -> dict[str, object]:
        now = self._clock.elapsed() / 1000.0
        dt = now - self._last if self._last else 1 / 60
        self._last = now
        try:
            levels = self._levels()
        except Exception:
            levels = {}
        self.uniforms = self.dynamics.update(dt, levels)
        self._fps_frames += 1
        elapsed = time.perf_counter() - self._fps_since
        if elapsed >= 1.0:
            self.fps = self._fps_frames / elapsed
            self._fps_frames = 0
            self._fps_since = time.perf_counter()
        return self.uniforms


class NexusCore(QOpenGLWidget, _CoreBase):
    gl_failed = Signal(str)

    def __init__(self, levels: LevelsSource, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(QSize(200, 200))
        self._scene: QOpenGLShaderProgram | None = None
        self._post: QOpenGLShaderProgram | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._fbo: QOpenGLFramebufferObject | None = None
        self._fbo_size = QSize()
        self._render_scale = 1.0
        self._slow_frames = 0
        self._ok = False
        self._init_driver(levels)

    # ------------------------------------------------------------- GL
    def _program(self, frag: str) -> QOpenGLShaderProgram:
        program = QOpenGLShaderProgram(self)
        vert = paths.asset("shaders", "fullscreen.vert").read_text(encoding="utf-8")
        frag_src = paths.asset("shaders", frag).read_text(encoding="utf-8")
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vert):
            raise RuntimeError("vertex shader: " + program.log())
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, frag_src):
            raise RuntimeError(f"{frag}: " + program.log())
        if not program.link():
            raise RuntimeError(f"link {frag}: " + program.log())
        return program

    def initializeGL(self) -> None:
        try:
            self._scene = self._program("core_scene.frag")
            self._post = self._program("core_post.frag")
            self._vao = QOpenGLVertexArrayObject(self)
            self._vao.create()
            self._ok = True
            log.info("NEXUS Core shaders compiled")
        except Exception as exc:
            message = str(exc)
            log.error("NEXUS Core GL init failed: %s", message)
            self._ok = False
            QTimer.singleShot(0, lambda: self.gl_failed.emit(message))

    def _ensure_fbo(self, w: int, h: int) -> None:
        size = QSize(max(1, int(w * self._render_scale)), max(1, int(h * self._render_scale)))
        if self._fbo is not None and self._fbo_size == size:
            return
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
        fmt.setMipmap(True)
        fmt.setInternalTextureFormat(GL_RGBA16F)
        self._fbo = QOpenGLFramebufferObject(size, fmt)
        self._fbo_size = size
        f = self.context().functions()
        f.glBindTexture(GL_TEXTURE_2D, self._fbo.texture())
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        f.glBindTexture(GL_TEXTURE_2D, 0)

    def paintGL(self) -> None:
        u = self._advance()
        f = self.context().functions()
        dpr = self.devicePixelRatioF()
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        if not self._ok or self._scene is None or self._post is None:
            f.glClearColor(0, 0, 0, 1)
            f.glClear(GL_COLOR_BUFFER_BIT)
            return
        started = time.perf_counter()
        self._ensure_fbo(w, h)
        assert self._fbo is not None and self._vao is not None
        sw, sh = self._fbo_size.width(), self._fbo_size.height()

        # pass 1: scene -> HDR texture
        self._fbo.bind()
        f.glViewport(0, 0, sw, sh)
        self._vao.bind()
        s = self._scene
        s.bind()
        s.setUniformValue("uRes", QVector2D(sw, sh))
        for name in ("uTime", "uRot", "uRing", "uFlow", "uScale", "uDistort", "uGlow", "uParticle", "uBass",
                     "uActivity", "uPulse", "uWarn", "uBoot"):
            s.setUniformValue1f(name, float(u[name]))  # type: ignore[arg-type]
        tint = u["uTint"]
        s.setUniformValue("uTint", QVector3D(*tint))  # type: ignore[misc]
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        s.release()
        self._fbo.release()

        f.glBindTexture(GL_TEXTURE_2D, self._fbo.texture())
        f.glGenerateMipmap(GL_TEXTURE_2D)

        # pass 2: post -> widget framebuffer
        f.glBindFramebuffer(0x8D40, self.defaultFramebufferObject())
        f.glViewport(0, 0, w, h)
        p = self._post
        p.bind()
        f.glActiveTexture(GL_TEXTURE0)
        f.glBindTexture(GL_TEXTURE_2D, self._fbo.texture())
        p.setUniformValue1i("uScene", 0)
        p.setUniformValue("uRes", QVector2D(w, h))
        for name in ("uTime", "uCA", "uBloom", "uGlitch", "uGrain", "uScan"):
            p.setUniformValue1f(name, float(u[name]))  # type: ignore[arg-type]
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        p.release()
        self._vao.release()
        f.glBindTexture(GL_TEXTURE_2D, 0)
        self._adapt((time.perf_counter() - started) * 1000)

    def _adapt(self, cpu_ms: float) -> None:
        # Keep 60 FPS on weak GPUs: render the scene at lower resolution when frames lag.
        if self.fps and self.fps < 50:
            self._slow_frames += 1
        else:
            self._slow_frames = max(0, self._slow_frames - 1)
        if self._slow_frames > 120 and self._render_scale > 0.55:
            self._render_scale = round(self._render_scale - 0.15, 2)
            self._slow_frames = 0
            log.info("NEXUS Core render scale -> %.2f (fps %.0f)", self._render_scale, self.fps)

    def cleanup(self) -> None:
        self._timer.stop()
        self.makeCurrent()
        self._fbo = None
        if self._vao is not None:
            self._vao.destroy()
        self.doneCurrent()


class PainterCore(QWidget, _CoreBase):
    """Software fallback with the same state/audio behaviour, drawn with QPainter."""

    def __init__(self, levels: LevelsSource, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        rnd = random.Random(7)
        self._specks = [(rnd.random() * math.tau, rnd.uniform(0.0, 1.0), rnd.random()) for _ in range(900)]
        self._init_driver(levels)

    def paintEvent(self, _event) -> None:  # noqa: ANN001
        u = self._advance()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        w, h = self.width(), self.height()
        m = min(w, h) * 0.5
        cx, cy = w / 2, h / 2 - 0.12 * m
        t = float(u["uTime"])
        boot = float(u["uBoot"])
        R = 0.33 * float(u["uScale"]) * m * (0.55 + 0.45 * boot)
        glow = float(u["uGlow"])

        halo = QRadialGradient(QPointF(cx, cy), R * 1.8)
        halo.setColorAt(0.5, QColor(190, 205, 255, int(60 * glow * boot)))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(halo)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), R * 1.8, R * 1.8)

        body = QRadialGradient(QPointF(cx - R * 0.3, cy - R * 0.35), R * 1.2)
        body.setColorAt(0.0, QColor(235, 238, 245, int(235 * boot)))
        body.setColorAt(0.7, QColor(120, 125, 135, int(200 * boot)))
        body.setColorAt(1.0, QColor(20, 20, 25, int(200 * boot)))
        painter.setBrush(body)
        painter.drawEllipse(QPointF(cx, cy), R, R)

        distort = float(u["uDistort"])
        rot = float(u["uRot"])
        painter.setPen(Qt.PenStyle.NoPen)
        for ang, rad, shade in self._specks:
            a = ang + rot * (0.5 + shade)
            rr = R * math.sqrt(rad) * (1.0 + distort * 0.15 * math.sin(a * 5 + t * 3))
            x, y = cx + math.cos(a) * rr, cy + math.sin(a) * rr * 0.98
            c = int(90 + 165 * shade)
            painter.setBrush(QColor(c, c, c, int(160 * boot)))
            painter.drawRect(QRectF(x, y, 1.2, 1.2))

        ring_pen = QPen(QColor(220, 228, 255, int(170 * boot)))
        ring_pen.setWidthF(1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(ring_pen)
        ring = float(u["uRing"])
        for radius, squash, base in ((1.46, 0.26, 24.0), (1.23, 0.39, 14.0)):
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(-(base + 6 * math.sin(ring * 0.3)))
            painter.drawEllipse(QPointF(0, 0), R * radius, R * radius * squash)
            painter.restore()
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(-36)
        painter.drawLine(QPointF(-m * 2.5, 0), QPointF(m * 2.5, 0))
        painter.restore()
        painter.end()

    def cleanup(self) -> None:
        self._timer.stop()


def create_core(levels: LevelsSource, parent: QWidget | None = None) -> QWidget:
    if QGuiApplication.instance() is not None and opengl_available():
        return NexusCore(levels, parent)
    log.warning("OpenGL 3.3 unavailable; using the software NEXUS Core")
    return PainterCore(levels, parent)
