// script.js для лендинга
const playButton = document.getElementById('playButton');
const radioStream = document.getElementById('radioStream');

// Джингл
const jingle = new Audio('/jingle-start.mp3');
jingle.preload = 'auto';

let isPlaying = false;
let streamPreloaded = false;

function togglePlay() {
    playButton.disabled = true;

    if (isPlaying) {
        radioStream.pause();
        radioStream.src = '';
        jingle.pause();
        playButton.textContent = 'PLAY';
        isPlaying = false;
        streamPreloaded = false;
        playButton.disabled = false;
        return;
    }

    // Параллельная предзагрузка стрима
    if (!streamPreloaded) {
        radioStream.src = 'https://vtrnk.online/radio_stream';
        radioStream.load();
        streamPreloaded = true;
        console.log("Preload стрима начат");
    }

    // Запуск джингла
    jingle.currentTime = 0;
    jingle.play().catch(err => {
        console.error("Ошибка джингла:", err);
        startStream();
    });

    jingle.onended = () => {
        startStream();
    };
}

function startStream() {
    radioStream.play().then(() => {
        console.log("Стрим запущен после джингла");
        playButton.textContent = 'STOP';
        isPlaying = true;
        playButton.disabled = false;
    }).catch(err => {
        console.error("Ошибка запуска стрима:", err);
        playButton.disabled = false;
    });
}

playButton.addEventListener('click', togglePlay);

// Обработка ошибок
radioStream.addEventListener('error', () => {
    console.error("Ошибка стрима");
    playButton.disabled = false;
});