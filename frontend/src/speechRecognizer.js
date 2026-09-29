/**
 * Web Speech API Voice Recognizer
 * Enables hands-free voice commands:
 * "entry 1, 2, 3, 4"
 * "side entry 1, 2, 3, 4"
 * "roam / roaming"
 * "dark mode / light mode"
 */
export class SpeechRecognizer {
  constructor(callbacks = {}) {
    this.callbacks = callbacks;
    this.isListening = false;
    this.recognition = null;
    this.supported = ('webkitSpeechRecognition' in window) || ('SpeechRecognition' in window);

    if (this.supported) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.lang = 'en-US';

      this.recognition.onstart = () => {
        this.isListening = true;
        if (this.callbacks.onStatusChange) this.callbacks.onStatusChange(true);
      };

      this.recognition.onend = () => {
        // Auto-restart if user still wants it active
        if (this.isListening) {
          try {
            this.recognition.start();
          } catch (e) {
            // Already started or busy
          }
        } else {
          if (this.callbacks.onStatusChange) this.callbacks.onStatusChange(false);
        }
      };

      this.recognition.onerror = (event) => {
        console.warn('Speech recognition event:', event.error);
        if (this.callbacks.onError) this.callbacks.onError(event.error);
      };

      this.recognition.onresult = (event) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const text = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += text;
          } else {
            interimTranscript += text;
          }
        }

        const transcriptToProcess = (finalTranscript || interimTranscript).toLowerCase().trim();
        if (this.callbacks.onTranscript) {
          this.callbacks.onTranscript(transcriptToProcess, Boolean(finalTranscript));
        }

        if (finalTranscript) {
          this.matchCommand(finalTranscript.toLowerCase());
        }
      };
    }
  }

  start() {
    if (!this.supported) {
      console.warn('Web Speech API is not supported in this browser.');
      return false;
    }
    this.isListening = true;
    try {
      this.recognition.start();
      return true;
    } catch (e) {
      console.warn('Speech start error:', e);
      return false;
    }
  }

  stop() {
    this.isListening = false;
    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch (e) {}
    }
  }

  matchCommand(text) {
    console.log('[Voice Recognizer] Heard:', text);

    // Side Entries first (more specific)
    if (text.includes('side entry 1') || text.includes('side 1') || text.includes('side entry one') || text.includes('left entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('side_entry_1');
    } else if (text.includes('side entry 2') || text.includes('side 2') || text.includes('side entry two') || text.includes('right entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('side_entry_2');
    } else if (text.includes('side entry 3') || text.includes('side 3') || text.includes('side entry three') || text.includes('top entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('side_entry_3');
    } else if (text.includes('side entry 4') || text.includes('side 4') || text.includes('side entry four') || text.includes('bottom entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('side_entry_4');
    }
    // Main Entries
    else if (text.includes('entry 1') || text.includes('entry one') || text.includes('first entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('entry_1');
    } else if (text.includes('entry 2') || text.includes('entry two') || text.includes('second entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('entry_2');
    } else if (text.includes('entry 3') || text.includes('entry three') || text.includes('third entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('entry_3');
    } else if (text.includes('entry 4') || text.includes('entry four') || text.includes('fourth entry')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('entry_4');
    }
    // Roaming
    else if (text.includes('roam') || text.includes('roaming') || text.includes('wander')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('toggle_roam');
    } else if (text.includes('stop roam') || text.includes('stop roaming') || text.includes('freeze') || text.includes('halt')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('stop_roam');
    }
    // Theme switching
    else if (text.includes('dark mode') || text.includes('dark')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('dark_mode');
    } else if (text.includes('light mode') || text.includes('light')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('light_mode');
    }
    // Reset / Center
    else if (text.includes('reset') || text.includes('center') || text.includes('home')) {
      if (this.callbacks.onCommand) this.callbacks.onCommand('reset');
    }
  }
}
