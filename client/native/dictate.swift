// On-device dictation for the Juno client.
// stdin:  "start" / "stop" lines, one dictation segment between them.
// stdout: one JSON line per recognition update, {"text": ..., "final": ...};
//         text is the whole segment so far. A segment that fails ends with
//         {"text": "", "final": true}.
import AVFoundation
import Speech

setvbuf(stdout, nil, _IOLBF, 0)

let recognizer = SFSpeechRecognizer()!
let engine = AVAudioEngine()
var request: SFSpeechAudioBufferRecognitionRequest?

func emit(_ text: String, final: Bool) {
    let data = try! JSONSerialization.data(withJSONObject: ["text": text, "final": final])
    print(String(data: data, encoding: .utf8)!)
}

func start() {
    let segment = SFSpeechAudioBufferRecognitionRequest()
    segment.requiresOnDeviceRecognition = true
    segment.shouldReportPartialResults = true
    request = segment
    recognizer.recognitionTask(with: segment) { result, error in
        if let result {
            emit(result.bestTranscription.formattedString, final: result.isFinal)
        } else if let error {
            fputs("recognition: \(error.localizedDescription)\n", stderr)
            emit("", final: true)
        }
    }
}

SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized, recognizer.supportsOnDeviceRecognition else {
        fputs("speech unavailable: authorization=\(status.rawValue) "
              + "onDevice=\(recognizer.supportsOnDeviceRecognition)\n", stderr)
        exit(1)
    }
    // The microphone stays open; audio only reaches a recognizer during a segment.
    let input = engine.inputNode
    input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
        request?.append(buffer)
    }
    try! engine.start()
    fputs("ready\n", stderr)

    Thread.detachNewThread {
        while let line = readLine() {
            DispatchQueue.main.async {
                if line == "start" {
                    start()
                } else if line == "stop" {
                    request?.endAudio()
                    request = nil
                }
            }
        }
        exit(0)
    }
}

RunLoop.main.run()
