/**
 * Noise gate and level meter for the live guitar channel.
 *
 * A gate rather than a downward expander: a single-coil through a high-gain
 * model hisses between phrases, and the thing that has to disappear is the
 * hiss, not the tail of the note. Hysteresis — a close threshold below the
 * open one — stops it chattering when a decaying note sits right on the line.
 *
 * No lookahead, deliberately. Lookahead would let the gate open before the
 * transient instead of on it, but every sample of it is added latency, and on
 * an instrument you are playing latency costs more than a slightly soft pick
 * attack. The attack is a few hundred microseconds instead.
 */
class GuitarProcessor extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [
      // dBFS. -100 is "off": nothing realistic sits below it.
      {name: "threshold", defaultValue: -100, minValue: -100, maxValue: 0,
       automationRate: "k-rate"},
      {name: "attack", defaultValue: 0.002, minValue: 0.0002, maxValue: 0.05,
       automationRate: "k-rate"},
      {name: "release", defaultValue: 0.12, minValue: 0.01, maxValue: 1.0,
       automationRate: "k-rate"},
      {name: "hold", defaultValue: 0.05, minValue: 0, maxValue: 1.0,
       automationRate: "k-rate"},
    ];
  }

  constructor() {
    super();
    this.envelope = 0;      // follower on the input
    this.gain = 0;          // what the gate is currently letting through
    this.holdLeft = 0;
    this.peak = 0;          // for the meter
    this.frames = 0;
    this.running = true;
    this.port.onmessage = (e) => {
      if (e.data === "stop") this.running = false;
    };
  }

  process(inputs, outputs, params) {
    const input = inputs[0];
    const output = outputs[0];
    if (!input || !input.length) {
      // No input yet: emit silence rather than stopping, or the node dies and
      // the channel has to be rebuilt when the device wakes up.
      if (output) output.forEach(ch => ch.fill(0));
      return this.running;
    }

    const threshold = params.threshold[0];
    const openAt = Math.pow(10, threshold / 20);
    // Close a little lower than it opens, or a decaying note chatters.
    const closeAt = openAt * 0.6;
    const attackCoef = Math.exp(-1 / (params.attack[0] * sampleRate));
    const releaseCoef = Math.exp(-1 / (params.release[0] * sampleRate));
    const holdFrames = params.hold[0] * sampleRate;
    const gateOff = threshold <= -99.5;

    const n = input[0].length;
    for (let i = 0; i < n; i++) {
      // Mono-sum for detection so a stereo input doesn't gate one side.
      let sample = 0;
      for (let c = 0; c < input.length; c++) sample += input[c][i];
      sample /= input.length;

      const level = Math.abs(sample);
      if (level > this.peak) this.peak = level;
      // The follower tracks up fast and down slowly, so the gate reacts to a
      // pick attack immediately but isn't fooled by the gaps inside a note.
      this.envelope = level > this.envelope
        ? level
        : level + (this.envelope - level) * releaseCoef;

      let target;
      if (gateOff) {
        target = 1;
      } else if (this.envelope > openAt) {
        target = 1;
        this.holdLeft = holdFrames;
      } else if (this.envelope > closeAt || this.holdLeft > 0) {
        target = 1;
        if (this.holdLeft > 0) this.holdLeft--;
      } else {
        target = 0;
      }

      const coef = target > this.gain ? attackCoef : releaseCoef;
      this.gain = target + (this.gain - target) * coef;

      for (let c = 0; c < output.length; c++) {
        const src = input[Math.min(c, input.length - 1)];
        output[c][i] = src[i] * this.gain;
      }
    }

    // The meter is for setting the interface's own gain knob, so it reports
    // the peak *before* the gate — a gated signal reads as silence and tells
    // you nothing about whether you are clipping the converter.
    this.frames += n;
    if (this.frames >= sampleRate / 20) {          // twenty times a second
      this.port.postMessage({peak: this.peak, gate: this.gain});
      this.peak = 0;
      this.frames = 0;
    }
    return this.running;
  }
}

registerProcessor("guitar-processor", GuitarProcessor);

/**
 * Records what reaches it, without touching the signal.
 *
 * A MediaRecorder would be less code, but it encodes to Opus in a container
 * and the point here is to get the exact samples that were played into a
 * stem. While recording, the samples are streamed to the page in batches,
 * each stamped with the audio clock's time of its first frame: that is what
 * lets the page draw the take as it comes in, place it on the song's
 * timeline exactly, and cut it at a loop boundary to start the next take.
 */
const BATCH = 2048;

class TakeRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.recording = false;
    this.batch = null;
    this.port.onmessage = (e) => {
      if (e.data === "start") { this.batch = null; this.recording = true; }
      else if (e.data === "stop") {
        this.flush();
        this.recording = false;
        this.port.postMessage({done: true});
      }
    };
  }

  flush() {
    const b = this.batch;
    this.batch = null;
    if (!b || !b.n) return;
    const data = b.chans.map(c => (b.n === BATCH ? c : c.slice(0, b.n)));
    this.port.postMessage({t: b.t, n: b.n, data}, data.map(a => a.buffer));
  }

  process(inputs, outputs) {
    const input = inputs[0];
    const output = outputs[0];
    if (input && input.length) {
      for (let c = 0; c < output.length; c++) {
        const src = input[Math.min(c, input.length - 1)];
        output[c].set(src);
      }
      if (this.recording) {
        const n = input[0].length;
        if (!this.batch) {
          this.batch = {t: currentTime, n: 0, chans: input.map(() => new Float32Array(BATCH))};
        }
        const b = this.batch;
        for (let c = 0; c < b.chans.length; c++) b.chans[c].set(input[Math.min(c, input.length - 1)], b.n);
        b.n += n;
        if (b.n + n > BATCH) this.flush();
      }
    } else if (output) {
      output.forEach(ch => ch.fill(0));
    }
    return true;
  }
}

registerProcessor("take-recorder", TakeRecorder);
