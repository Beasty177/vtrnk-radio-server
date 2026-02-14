document.addEventListener('DOMContentLoaded', function() {
    const playButton = document.getElementById('playButton');
    const audio = document.getElementById('radioStream');

    playButton.addEventListener('click', function() {
        if (audio.paused) {
            audio.play();
            playButton.textContent = 'PAUSE';
        } else {
            audio.pause();
            playButton.textContent = 'PLAY';
        }
    });
});