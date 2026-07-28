// What MediaRecorder will actually record in, which differs by browser (webm on
// Chrome/Firefox, mp4 on Safari). Downloads are re-encoded to MP3 from whichever
// of these we get — see encodeMp3.ts.

const getVideoMimeType = () => {
  if (!MediaRecorder.isTypeSupported){
    return "video/mp4";
  }
  if (MediaRecorder.isTypeSupported("video/webm")) {
    return "video/webm";
  }
  if (MediaRecorder.isTypeSupported("video/mp4")) {
    return "video/mp4";
  }
  console.log("No supported video mime type found")
  return "";
};

const getAudioMimeType = () => {
  if (!MediaRecorder.isTypeSupported){
    return "audio/mp4";
  }
  if (MediaRecorder.isTypeSupported("audio/webm")) {
    return "audio/webm";
  }
  if (MediaRecorder.isTypeSupported("audio/mpeg")) {
    return "audio/mpeg";
  }
  if (MediaRecorder.isTypeSupported("audio/mp4")) {
    return "audio/mp4";
  }
  console.log("No supported audio mime type found")
  return "";
}

export const getMimeType = (type: "audio" | "video") => {
  if(type === "audio") {
    return getAudioMimeType();
  }
  return getVideoMimeType();
}

export const getExtension = (type: "audio" | "video") => {
  if(getMimeType(type).includes("mp4")) {
    return "mp4";
  }
  if(getMimeType(type).includes("mpeg")) {
    return "mp3";
  }
  return "webm";
}