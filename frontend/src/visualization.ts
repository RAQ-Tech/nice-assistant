import { sharedAudioContext } from './audio_context';

interface Star {
  x: number;
  y: number;
  size: number;
  phase: number;
}

export class Visualizer {
  private analyser: AnalyserNode | null = null;
  private frequencies = new Uint8Array(0);
  private canvas: HTMLCanvasElement | null = null;
  private enabled = false;
  private frame = 0;
  private readonly stars: Star[] = Array.from({ length: 140 }, () => ({
    x: Math.random(),
    y: Math.random(),
    size: 0.4 + Math.random() * 1.6,
    phase: Math.random() * Math.PI * 2,
  }));

  constructor(private readonly audio: HTMLAudioElement) {}

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (enabled) this.start();
    else this.halt();
  }

  /**
   * Route the reply's sound through an analyser, so the rings move with it.
   *
   * Only while the visualizer is on. Once an element is routed through Web
   * Audio it cannot be unrouted, and from then on it is silent whenever that
   * context is - which on a phone is whenever the system has paused audio.
   * With the visualizer off, nothing here touches the element and it plays on
   * its own. It uses the page's one context, kept running by taps.
   */
  connectAudio(): void {
    if (this.analyser || !this.enabled) return;
    const context = sharedAudioContext();
    if (!context) return;
    const source = context.createMediaElementSource(this.audio);
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 512;
    this.frequencies = new Uint8Array(this.analyser.frequencyBinCount);
    source.connect(this.analyser);
    this.analyser.connect(context.destination);
  }

  node(): HTMLCanvasElement {
    const canvas = document.createElement('canvas');
    canvas.id = 'vizCanvas';
    this.canvas = canvas;
    this.resize();
    if (this.enabled) this.start();
    return canvas;
  }

  dispose(): void {
    this.halt();
    this.canvas = null;
  }

  // Drawing runs only while the visualizer is on. It used to run sixty times a
  // second for the life of the page, drawing nothing, on a phone's battery.
  private start(): void {
    if (this.frame) return;
    const loop = (time: number): void => {
      if (!this.enabled) {
        this.frame = 0;
        return;
      }
      this.frame = requestAnimationFrame(loop);
      this.draw(time);
    };
    this.frame = requestAnimationFrame(loop);
  }

  private halt(): void {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }

  private resize(): void {
    if (!this.canvas) return;
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    const width = window.visualViewport?.width ?? window.innerWidth;
    const height = window.visualViewport?.height ?? window.innerHeight;
    this.canvas.width = Math.floor(width * ratio);
    this.canvas.height = Math.floor(height * ratio);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
  }

  private draw(time: number): void {
    const canvas = this.canvas;
    if (!canvas || !canvas.isConnected) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    this.resize();
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    const width = canvas.width / ratio;
    const height = canvas.height / ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    let energy = 0.03;
    if (this.analyser && this.frequencies.length) {
      this.analyser.getByteFrequencyData(this.frequencies);
      energy = this.frequencies.reduce((sum, item) => sum + item / 255, 0) / this.frequencies.length;
    }
    const background = context.createLinearGradient(0, 0, width, height);
    background.addColorStop(0, 'rgba(2, 8, 20, .88)');
    background.addColorStop(0.5, 'rgba(5, 16, 38, .68)');
    background.addColorStop(1, 'rgba(2, 8, 20, .9)');
    context.fillStyle = background;
    context.fillRect(0, 0, width, height);
    for (const star of this.stars) {
      const alpha = 0.08 + Math.max(0, Math.sin(time * 0.001 + star.phase)) * 0.18 + energy * 0.35;
      context.fillStyle = `rgba(140, 255, 245, ${alpha})`;
      context.beginPath();
      context.arc(star.x * width, star.y * height, star.size, 0, Math.PI * 2);
      context.fill();
    }
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.min(width, height) * (0.18 + energy * 0.08);
    const glow = context.createRadialGradient(centerX, centerY, radius * 0.1, centerX, centerY, radius * 1.8);
    glow.addColorStop(0, `rgba(166, 252, 255, ${0.28 + energy * 0.3})`);
    glow.addColorStop(0.45, `rgba(82, 162, 255, ${0.14 + energy * 0.2})`);
    glow.addColorStop(1, 'rgba(26, 56, 118, 0)');
    context.fillStyle = glow;
    context.beginPath();
    context.arc(centerX, centerY, radius * 1.8, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = `rgba(114, 248, 255, ${0.18 + energy * 0.5})`;
    context.lineWidth = 1.5;
    context.beginPath();
    context.arc(centerX, centerY, radius, 0, Math.PI * 2);
    context.stroke();
  }
}
