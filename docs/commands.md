# Каталог поддерживаемых команд

В словаре **392 команды и группы команд**, **3 892 формулировки задач** в **15 категориях**. Они ссылаются на **1 920 разных строк шаблонов**.

Одна строка таблицы соответствует записи словаря, а не обязательно отдельной программе. Например, `git commit` — подкоманда Git, а `perl text substitution` — тематическая группа рецептов Perl. Одна утилита может встречаться в разных категориях для разных задач.

Столбец «Автоподстановка» показывает число размеченных шаблонов относительно всех шаблонов этой записи. «—» означает, что автоматической подстановки аргументов нет; найденный шаблон можно отредактировать вручную. Даже при наличии разметки она поддерживается не во всех вариантах команды: всего размечено **59 шаблонов**. Синтаксис в таблице описывает утилиту в целом; конкретные варианты и примеры берутся из словаря.

Примеры под каждой категорией приведены из словаря. Метка ⚠ у примера соответствует признаку `destructive`: меню запрашивает подтверждение. Часть утилит относится к конкретной ОС или устанавливается отдельно. Назначение, разбор аргументов и ограничения поиска описаны в [README](../README.md#использование).

## Категории

| Категория | Команд и групп | Формулировок |
|---|---:|---:|
| [Файлы и каталоги](#filesystem) | 34 | 312 |
| [Обработка текста](#text_processing) | 34 | 272 |
| [Поиск](#search) | 14 | 142 |
| [Сеть](#networking) | 33 | 286 |
| [Система](#system) | 34 | 308 |
| [Процессы](#process) | 32 | 280 |
| [Права и пользователи](#permissions) | 21 | 192 |
| [Архивы и сжатие](#compression) | 16 | 156 |
| [Диски](#disk) | 20 | 184 |
| [Git](#git) | 30 | 382 |
| [Docker и Kubernetes](#docker) | 22 | 204 |
| [Менеджеры пакетов](#package_managers) | 17 | 250 |
| [Однострочные команды Perl](#perl_oneliners) | 21 | 422 |
| [macOS](#macos) | 19 | 116 |
| [Другие инструменты](#misc) | 45 | 386 |

<a id="filesystem"></a>

## Файлы и каталоги

Источник: [filesystem.json](../src/cf/data/commands/filesystem.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `ls` | Просмотр содержимого каталога | `ls [options] [file...]` | — |
| `cp` | Копирование файлов и каталогов | `cp [options] source dest` | 4 из 5 |
| `mv` | Перемещение и переименование файлов и каталогов | `mv [options] source dest` | 2 из 3 |
| `rm` | Удаление файлов и каталогов | `rm [options] file...` | 4 из 5 |
| `mkdir` | Создание каталогов | `mkdir [options] directory...` | — |
| `rmdir` | Удаление пустых каталогов | `rmdir [options] directory...` | 3 из 3 |
| `touch` | Создание пустых файлов и изменение временных меток | `touch [options] file...` | — |
| `ln` | Создание жёстких и символических ссылок | `ln [options] target link_name` | — |
| `stat` | Просмотр подробных свойств файла или файловой системы | `stat [options] file...` | 5 из 5 |
| `file` | Определение типа файла | `file [options] file...` | 3 из 4 |
| `basename` | Выделение имени файла без пути и необязательного суффикса | `basename string [suffix]` | — |
| `dirname` | Выделение каталога из пути к файлу | `dirname string` | — |
| `realpath` | Определение абсолютного пути с разрешением символических ссылок | `realpath [options] file...` | — |
| `pwd` | Вывод текущего рабочего каталога | `pwd [options]` | — |
| `cd` | Смена текущего рабочего каталога | `cd [directory]` | — |
| `tree` | Просмотр структуры каталогов в виде дерева | `tree [options] [directory...]` | — |
| `du` | Оценка места, занятого файлами и каталогами | `du [options] [file...]` | 2 из 5 |
| `df` | Просмотр свободного и занятого места в файловых системах | `df [options] [file...]` | — |
| `mount` | Монтирование файловой системы | `mount [options] device directory` | — |
| `umount` | Отмонтирование файловой системы | `umount [options] directory\|device` | — |
| `mktemp` | Безопасное создание временного файла или каталога | `mktemp [options] [template]` | — |
| `install` | Копирование файлов с установкой прав и атрибутов | `install [options] source dest` | — |
| `shred` | Перезапись содержимого файла перед удалением | `shred [options] file...` | — |
| `truncate` | Уменьшение или увеличение размера файла | `truncate [options] file...` | — |
| `fallocate` | Выделение и освобождение места для файла | `fallocate [options] file` | — |
| `sync` | Сброс кешей файловых систем на диск | `sync [options] [file...]` | — |
| `chroot` | Запуск команды с другим корневым каталогом | `chroot [options] newroot [command]` | — |
| `dd` | Низкоуровневое копирование и преобразование данных | `dd [operand...]` | — |
| `rsync` | Синхронизация файлов и каталогов локально или по сети | `rsync [options] source destination` | — |
| `rename` | Массовое переименование файлов по шаблону | `rename [options] expression replacement file...` | — |
| `readlink` | Просмотр цели символической ссылки | `readlink [options] file` | — |
| `inotifywait` | Ожидание изменений файлов через inotify | `inotifywait [options] file1 [file2] [...]` | — |
| `fswatch` | Отслеживание изменений файлов на разных платформах | `fswatch [options] ... path ...` | — |
| `dirs` | Просмотр сохранённого стека каталогов | `dirs [-clpv]` | — |

Пример запроса: `скопировать файл в другой файл`.

Шаблон:

```bash
cp -- <source> <destination>
```

<a id="text_processing"></a>

## Обработка текста

Источник: [text_processing.json](../src/cf/data/commands/text_processing.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `grep` | Поиск строк по шаблону в файлах | `grep [options] pattern [file...]` | — |
| `sed` | Потоковое редактирование и преобразование текста | `sed [options] 'command' [file...]` | — |
| `awk` | Обработка текста и полей языком awk | `awk [options] 'program' [file...]` | — |
| `cut` | Выделение частей строк и столбцов | `cut [options] [file...]` | — |
| `tr` | Замена и удаление символов | `tr [options] set1 [set2]` | — |
| `sort` | Сортировка строк текста | `sort [options] [file...]` | — |
| `uniq` | Поиск и удаление повторяющихся соседних строк | `uniq [options] [input [output]]` | — |
| `wc` | Подсчёт строк, слов и байтов | `wc [options] [file...]` | — |
| `head` | Вывод начала файла | `head [options] [file...]` | 4 из 4 |
| `tail` | Вывод конца файла | `tail [options] [file...]` | 4 из 5 |
| `tee` | Одновременная запись стандартного ввода в файлы и стандартный вывод | `tee [options] [file...]` | — |
| `paste` | Объединение строк файлов по столбцам | `paste [options] [file...]` | — |
| `join` | Объединение двух отсортированных файлов по общему полю | `join [options] file1 file2` | — |
| `comm` | Построчное сравнение двух отсортированных файлов | `comm [options] file1 file2` | — |
| `diff` | Просмотр различий между файлами | `diff [options] file1 file2` | — |
| `patch` | Применение файла различий к исходным файлам | `patch [options] [original [patchfile]]` | — |
| `column` | Оформление текста в виде таблицы или нескольких столбцов | `column [options] [file...]` | — |
| `fold` | Перенос строк по заданной ширине | `fold [options] [file...]` | — |
| `fmt` | Переформатирование абзацев текста | `fmt [options] [file...]` | — |
| `expand` | Замена табуляции пробелами | `expand [options] [file...]` | — |
| `unexpand` | Замена пробелов символами табуляции | `unexpand [options] [file...]` | — |
| `nl` | Нумерация строк файлов | `nl [options] [file...]` | — |
| `rev` | Разворот порядка символов в строках | `rev [file...]` | — |
| `tac` | Вывод строк файлов в обратном порядке | `tac [options] [file...]` | — |
| `strings` | Извлечение печатных строк из двоичных файлов | `strings [options] [file...]` | — |
| `iconv` | Преобразование кодировок текста | `iconv [options] [file...]` | — |
| `cat` | Объединение файлов и вывод их содержимого | `cat [options] [file...]` | 2 из 4 |
| `less` | Интерактивный постраничный просмотр текста с прокруткой | `less [options] [file...]` | — |
| `more` | Простой постраничный просмотр текста | `more [options] [file...]` | — |
| `split` | Разбиение большого файла на части | `split [options] [input [prefix]]` | — |
| `dos2unix` | Преобразование окончаний строк DOS и Unix | `dos2unix [options] [file...]` | — |
| `xxd` | Создание шестнадцатеричного представления данных и обратное преобразование | `xxd [options] [infile [outfile]]` | — |
| `envsubst` | Подстановка переменных окружения в текст | `envsubst [SHELL-FORMAT]` | — |
| `sponge` | Чтение стандартного ввода целиком перед записью в файл | `sponge [file]` | — |

Пример запроса: `показать первые десять строк файла`.

Шаблон:

```bash
head -- <files>
```

<a id="search"></a>

## Поиск

Источник: [search.json](../src/cf/data/commands/search.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `find` | Поиск файлов в дереве каталогов | `find [path...] [expression]` | — |
| `locate` | Поиск файлов по имени в готовом индексе | `locate [options] pattern` | — |
| `which` | Поиск исполняемого файла в PATH | `which [options] command` | — |
| `whereis` | Поиск исполняемого файла, исходников и страницы руководства | `whereis [options] command` | — |
| `whatis` | Просмотр краткого описания команды | `whatis command` | — |
| `apropos` | Поиск по названиям и описаниям страниц руководства | `apropos [options] keyword` | — |
| `type` | Определение способа интерпретации имени команды оболочкой | `type [options] name` | — |
| `command` | Запуск команды в обход функций и псевдонимов оболочки | `command [options] command_name [args]` | — |
| `fd` | Быстрый поиск файлов утилитой fd | `fd [options] [pattern] [path...]` | — |
| `rg` | Быстрый рекурсивный поиск текста утилитой ripgrep | `rg [options] pattern [path...]` | — |
| `ag` | Поиск текста в исходном коде утилитой The Silver Searcher | `ag [options] pattern [path...]` | — |
| `fzf` | Интерактивный нечёткий поиск и выбор строк | `fzf [options]` | — |
| `xargs` | Построение и запуск команд из стандартного ввода | `xargs [options] [command [initial-args]]` | — |
| `parallel` | Параллельный запуск заданий на нескольких ядрах CPU | `parallel [options] command [arguments]` | — |

Пример запроса: `найти Python-файлы в текущей папке и всех подпапках`.

Шаблон:

```bash
find . -type f -name '*.py'
```

<a id="networking"></a>

## Сеть

Источник: [networking.json](../src/cf/data/commands/networking.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `curl` | Передача данных по сетевым протоколам | `curl [options] [URL]` | — |
| `wget` | Неинтерактивная загрузка файлов по HTTP, HTTPS и FTP | `wget [options] [URL]` | — |
| `ssh` | Подключение к удалённой системе по SSH | `ssh [options] [user@]hostname [command]` | — |
| `ssh-keygen` | Создание, преобразование и управление ключами SSH | `ssh-keygen [-t type] [-f path] [-C comment]` | — |
| `ssh-add` | Добавление закрытых ключей в ssh-agent | `ssh-add [options] [file ...]` | — |
| `scp` | Защищённое копирование файлов по SSH | `scp [options] source destination` | — |
| `sftp` | Интерактивная передача файлов по SFTP | `sftp [options] [user@]host[:path]` | — |
| `rsync` | Синхронизация файлов локально и по сети | `rsync [options] source destination` | — |
| `ping` | Проверка доступности узла пакетами ICMP | `ping [options] destination` | — |
| `traceroute` | Определение маршрута пакетов до узла | `traceroute [options] host` | — |
| `dig` | Запрос DNS-записей у серверов имён | `dig [@server] [name] [type]` | — |
| `nslookup` | Интерактивные и обычные DNS-запросы | `nslookup [host] [server]` | — |
| `host` | Простые DNS-запросы | `host [options] name [server]` | — |
| `nc` | Чтение и запись данных через сетевые соединения Netcat | `nc [options] [hostname] [port]` | — |
| `ss` | Просмотр сокетов и сетевых соединений | `ss [options] [filter]` | — |
| `netstat` | Просмотр соединений, таблиц маршрутизации и сетевой статистики | `netstat [options]` | — |
| `ip` | Просмотр и настройка сетевых интерфейсов, маршрутов и туннелей | `ip [options] object command` | — |
| `ifconfig` | Просмотр и настройка сетевых интерфейсов устаревшей утилитой | `ifconfig [interface] [options]` | — |
| `arp` | Просмотр и изменение кеша ARP | `arp [options] [hostname]` | — |
| `route` | Просмотр и изменение таблицы IP-маршрутизации | `route [options] [command] [target]` | — |
| `nmap` | Исследование узлов, портов и служб сети | `nmap [scan type] [options] target` | — |
| `tcpdump` | Захват и анализ сетевого трафика | `tcpdump [options] [expression]` | — |
| `iptables` | Настройка фильтрации IPv4-пакетов и NAT | `iptables [options] [chain] [rule]` | — |
| `ufw` | Управление правилами сетевого экрана через UFW | `ufw [options] [rule]` | — |
| `lsof` | Просмотр открытых файлов и сетевых соединений процессов | `lsof [options]` | — |
| `mtr` | Диагностика сети с проверкой маршрута и потерь пакетов | `mtr [options] hostname` | — |
| `whois` | Просмотр регистрационных данных домена или IP-адреса | `whois [options] name` | — |
| `telnet` | Проверка сетевых служб через протокол Telnet | `telnet [options] [host [port]]` | — |
| `ab` | Нагрузочное тестирование HTTP-сервера утилитой Apache Benchmark | `ab [options] URL` | — |
| `httpie` | Отправка HTTP-запросов удобным интерфейсом HTTPie | `http [options] [METHOD] URL [items]` | — |
| `ssh-copy-id` | Установка открытого ключа в authorized_keys удалённого пользователя | `ssh-copy-id [-i key] [user@]hostname` | — |
| `socat` | Перенаправление потоков данных между сокетами и другими каналами | `socat [options] <address> <address>` | — |
| `iperf3` | Измерение пропускной способности сети | `iperf3 -s \| -c host [options]` | — |

Пример запроса: `скачать файл по ссылке с исходным именем`.

Шаблон:

```bash
curl -O https://example.com/file.tar.gz
```

<a id="system"></a>

## Система

Источник: [system.json](../src/cf/data/commands/system.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `uname` | Просмотр сведений об операционной системе и оборудовании | `uname [options]` | — |
| `hostname` | Просмотр и изменение имени узла | `hostname [options] [name]` | — |
| `uptime` | Просмотр времени работы и средней нагрузки системы | `uptime [options]` | — |
| `date` | Просмотр и установка системной даты и времени | `date [options] [+format]` | — |
| `cal` | Вывод календаря в терминале | `cal [options] [[month] year]` | — |
| `timedatectl` | Настройка времени, даты и часового пояса | `timedatectl [command]` | — |
| `systemctl` | Управление системой и службами systemd | `systemctl [command] [unit]` | — |
| `service` | Управление службами через сценарии System V | `service name command` | — |
| `journalctl` | Просмотр журнала systemd | `journalctl [options]` | — |
| `dmesg` | Просмотр сообщений кольцевого буфера ядра | `dmesg [options]` | — |
| `sysctl` | Просмотр и изменение параметров ядра | `sysctl [options] [variable[=value]]` | — |
| `lscpu` | Просмотр архитектуры и характеристик CPU | `lscpu [options]` | — |
| `lsblk` | Просмотр дисков, разделов и других блочных устройств | `lsblk [options] [device]` | — |
| `lspci` | Просмотр устройств PCI | `lspci [options]` | — |
| `lsusb` | Просмотр устройств USB | `lsusb [options]` | — |
| `lshw` | Просмотр подробной конфигурации оборудования | `lshw [options]` | — |
| `dmidecode` | Просмотр сведений об оборудовании из таблиц BIOS и DMI | `dmidecode [options]` | — |
| `free` | Просмотр свободной и занятой оперативной памяти | `free [options]` | — |
| `vmstat` | Просмотр статистики памяти, процессора и ввода-вывода | `vmstat [options] [delay [count]]` | — |
| `iostat` | Просмотр статистики CPU и устройств ввода-вывода | `iostat [options] [interval [count]]` | — |
| `sar` | Сбор, сохранение и просмотр статистики системы | `sar [options] [interval [count]]` | — |
| `who` | Просмотр пользователей, вошедших в систему | `who [options]` | — |
| `w` | Просмотр пользователей и их текущей активности | `w [options] [user]` | — |
| `last` | Просмотр истории входов пользователей | `last [options] [username]` | — |
| `lastb` | Просмотр неудачных попыток входа | `lastb [options] [username]` | — |
| `id` | Просмотр идентификаторов пользователя и групп | `id [options] [user]` | — |
| `groups` | Просмотр групп пользователя | `groups [user]` | — |
| `env` | Просмотр и изменение окружения запускаемой команды | `env [options] [name=value] [command]` | — |
| `printenv` | Просмотр переменных окружения | `printenv [variable]` | — |
| `set` | Просмотр и изменение параметров оболочки | `set [options] [args]` | — |
| `export` | Экспорт переменных окружения в дочерние процессы | `export [name[=value]]` | — |
| `ulimit` | Просмотр и настройка ограничений ресурсов оболочки | `ulimit [options] [limit]` | — |
| `locale` | Просмотр языковых и региональных настроек | `locale [options] [name]` | — |
| `hostnamectl` | Просмотр и изменение имени узла и связанных настроек | `hostnamectl [command]` | — |

Пример запроса: `показать сведения о ядре, имени компьютера и архитектуре`.

Шаблон:

```bash
uname -a
```

<a id="process"></a>

## Процессы

Источник: [process.json](../src/cf/data/commands/process.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `ps` | Просмотр текущих процессов | `ps [options]` | — |
| `top` | Просмотр процессов и потребления ресурсов в реальном времени | `top [options]` | — |
| `htop` | Интерактивный просмотр процессов через htop | `htop [options]` | — |
| `kill` | Отправка сигнала процессу по PID | `kill [-signal] pid [...]` | — |
| `killall` | Отправка сигналов процессам по имени | `killall [options] name [...]` | — |
| `pkill` | Отправка сигналов процессам, соответствующим шаблону | `pkill [options] pattern` | — |
| `pgrep` | Поиск PID процессов по имени и другим признакам | `pgrep [options] pattern` | — |
| `nice` | Запуск команды с заданным приоритетом планирования | `nice [-n adjustment] command [args]` | — |
| `renice` | Изменение приоритета работающего процесса | `renice [-n] priority [-p pid] [-g pgrp] [-u user]` | — |
| `nohup` | Запуск команды с защитой от сигнала отключения терминала | `nohup command [args]` | — |
| `bg` | Продолжение приостановленного задания в фоне | `bg [jobspec]` | — |
| `fg` | Перенос фонового задания на передний план | `fg [jobspec]` | — |
| `jobs` | Просмотр заданий текущей оболочки | `jobs [options] [jobspec]` | — |
| `wait` | Ожидание завершения фоновых процессов | `wait [pid\|jobspec]` | — |
| `disown` | Исключение задания из управления оболочкой | `disown [-h] [-ar] [jobspec]` | — |
| `screen` | Работа с сохраняемыми терминальными сеансами screen | `screen [options] [command [args]]` | — |
| `tmux` | Управление несколькими терминальными сеансами tmux | `tmux [command [flags]]` | — |
| `watch` | Периодический запуск команды с обновлением вывода | `watch [options] command` | — |
| `timeout` | Запуск команды с ограничением времени выполнения | `timeout [options] duration command [args]` | — |
| `time` | Измерение времени выполнения команды | `time command [args]` | — |
| `strace` | Трассировка системных вызовов и сигналов процесса | `strace [options] command [args]` | — |
| `ltrace` | Трассировка вызовов библиотек процесса | `ltrace [options] command [args]` | — |
| `lsof` | Просмотр открытых файлов и использующих их процессов | `lsof [options]` | — |
| `fuser` | Поиск процессов, использующих файл или сокет | `fuser [options] name [...]` | — |
| `pstree` | Просмотр дерева процессов | `pstree [options] [pid\|user]` | — |
| `crontab` | Настройка периодического выполнения команд через cron | `crontab [-u user] [-l \| -e \| -r]` | — |
| `at` | Планирование однократного запуска команды | `at [options] time` | — |
| `batch` | Запуск отложенной команды при низкой нагрузке системы | `batch` | — |
| `systemd-run` | Запуск временной службы или области systemd | `systemd-run [options] command [args]` | — |
| `iotop` | Просмотр дисковой активности процессов | `iotop [options]` | — |
| `taskset` | Просмотр и настройка привязки процессов к ядрам CPU | `taskset [options] mask command` | — |
| `ionice` | Просмотр и настройка приоритета дискового ввода-вывода | `ionice [-c class] [-n level] [command]` | — |

Пример запроса: `показать все процессы с пользователями и расходом CPU и памяти`.

Шаблон:

```bash
ps aux
```

<a id="permissions"></a>

## Права и пользователи

Источник: [permissions.json](../src/cf/data/commands/permissions.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `chmod` | Изменение прав доступа к файлам | `chmod [options] mode file [...]` | — |
| `chown` | Изменение владельца и группы файла | `chown [options] owner[:group] file [...]` | — |
| `chgrp` | Изменение группы файла | `chgrp [options] group file [...]` | — |
| `umask` | Настройка маски прав для новых файлов и каталогов | `umask [-S] [mask]` | — |
| `sudo` | Выполнение команды от имени другого пользователя | `sudo [options] command [args]` | — |
| `su` | Переключение на другую учётную запись | `su [options] [user]` | — |
| `visudo` | Безопасное редактирование sudoers с проверкой синтаксиса | `visudo [options]` | — |
| `setfacl` | Настройка списков управления доступом ACL | `setfacl [options] [{-m\|-x} acl_spec] file [...]` | — |
| `getfacl` | Просмотр списков управления доступом ACL | `getfacl [options] file [...]` | — |
| `chattr` | Изменение специальных атрибутов файлов Linux | `chattr [options] [+-=attribute] file [...]` | — |
| `lsattr` | Просмотр специальных атрибутов файлов Linux | `lsattr [options] [file ...]` | — |
| `passwd` | Изменение пароля пользователя | `passwd [options] [username]` | — |
| `useradd` | Создание учётной записи пользователя | `useradd [options] username` | — |
| `userdel` | Удаление учётной записи пользователя | `userdel [options] username` | — |
| `usermod` | Изменение свойств учётной записи пользователя | `usermod [options] username` | — |
| `groupadd` | Создание группы | `groupadd [options] group` | — |
| `groupdel` | Удаление группы | `groupdel [options] group` | — |
| `groupmod` | Изменение свойств группы | `groupmod [options] group` | — |
| `newgrp` | Переключение текущего идентификатора группы | `newgrp [-] [group]` | — |
| `chpasswd` | Пакетное изменение паролей из стандартного ввода | `chpasswd [options]` | — |
| `adduser` | Интерактивное добавление пользователя | `adduser [options] user` | — |

Пример запроса: `сделать скрипт исполняемым`.

Шаблон:

```bash
chmod +x script.sh
```

<a id="compression"></a>

## Архивы и сжатие

Источник: [compression.json](../src/cf/data/commands/compression.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `tar` | Создание архивов с необязательным сжатием | `tar [options] [file ...]` | 5 из 6 |
| `gzip` | Сжатие файлов алгоритмом gzip | `gzip [options] [file ...]` | 5 из 5 |
| `gunzip` | Распаковка файлов gzip | `gunzip [options] [file ...]` | — |
| `bzip2` | Сжатие файлов алгоритмом bzip2 | `bzip2 [options] [file ...]` | 4 из 5 |
| `xz` | Сжатие файлов алгоритмом LZMA2 | `xz [options] [file ...]` | 5 из 5 |
| `zip` | Создание сжатых ZIP-архивов | `zip [options] zipfile files [...]` | 3 из 5 |
| `unzip` | Извлечение файлов из ZIP-архивов | `unzip [options] zipfile [file ...] [-d dir]` | 4 из 5 |
| `7z` | Создание и распаковка архивов 7-Zip | `7z [command] [options] archive [files ...]` | — |
| `zcat` | Вывод содержимого gzip-файлов без отдельной распаковки | `zcat [file ...]` | — |
| `zless` | Постраничный просмотр сжатых файлов | `zless [file ...]` | — |
| `zgrep` | Поиск текста в сжатых файлах без отдельной распаковки | `zgrep [options] pattern [file ...]` | — |
| `rar` | Создание архивов RAR и управление ими | `rar [command] [options] archive [files ...]` | — |
| `unrar` | Извлечение файлов из архивов RAR | `unrar [command] [options] archive [files ...]` | — |
| `pigz` | Параллельное сжатие gzip на нескольких ядрах CPU | `pigz [options] [file ...]` | — |
| `lz4` | Быстрое сжатие и распаковка алгоритмом LZ4 | `lz4 [options] [file ...]` | — |
| `zstd` | Сжатие и распаковка алгоритмом Zstandard | `zstd [options] [file ...]` | — |

Пример запроса: `упаковать папку в архив tar.gz`.

Шаблон:

```bash
tar -czf <archive> -- <source>
```

<a id="disk"></a>

## Диски

Источник: [disk.json](../src/cf/data/commands/disk.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `fdisk` | Управление таблицами разделов MBR | `fdisk [options] device` | — |
| `gdisk` | Интерактивное управление таблицами разделов GPT | `gdisk [options] device` | — |
| `parted` | Управление разделами диска с поддержкой MBR и GPT | `parted [options] [device [command...]]` | — |
| `mkfs` | Создание файловой системы Linux на разделе | `mkfs [options] [-t type] device` | — |
| `fsck` | Проверка и восстановление файловой системы Linux | `fsck [options] [filesystem...]` | — |
| `mount` | Монтирование файловой системы в каталог | `mount [options] device directory` | — |
| `umount` | Отмонтирование файловой системы | `umount [options] directory\|device` | — |
| `lsblk` | Просмотр блочных устройств | `lsblk [options] [device...]` | — |
| `blkid` | Просмотр атрибутов блочных устройств | `blkid [options] [device...]` | — |
| `tune2fs` | Настройка параметров файловых систем ext2, ext3 и ext4 | `tune2fs [options] device` | — |
| `e2fsck` | Проверка файловой системы ext2, ext3 или ext4 | `e2fsck [options] device` | — |
| `badblocks` | Поиск повреждённых блоков на устройстве | `badblocks [options] device` | — |
| `hdparm` | Просмотр и настройка параметров устройств SATA и IDE | `hdparm [options] device` | — |
| `smartctl` | Контроль состояния накопителей с поддержкой SMART | `smartctl [options] device` | — |
| `mdadm` | Управление программными RAID-массивами Linux | `mdadm [mode] [options] [device]` | — |
| `lvs` | Просмотр логических томов LVM | `lvs [options] [LogicalVolume...]` | — |
| `vgs` | Просмотр групп томов LVM | `vgs [options] [VolumeGroup...]` | — |
| `pvs` | Просмотр физических томов LVM | `pvs [options] [PhysicalVolume...]` | — |
| `lvcreate` | Создание логического тома в группе LVM | `lvcreate [options] VolumeGroup` | — |
| `ncdu` | Интерактивный анализ занятого места на диске | `ncdu [options] [directory]` | — |

Пример запроса: `показать таблицы разделов всех обнаруженных дисков`.

Шаблон:

```bash
sudo fdisk -l
```

<a id="git"></a>

## Git

Источник: [git.json](../src/cf/data/commands/git.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `git init` | Создание или повторная инициализация репозитория Git | `git init [directory]` | — |
| `git clone` | Клонирование репозитория в новый каталог | `git clone [options] <repository> [directory]` | — |
| `git add` | Добавление изменений в индекс Git | `git add [options] [pathspec...]` | — |
| `git commit` | Сохранение изменений в коммите | `git commit [options]` | — |
| `git push` | Отправка объектов и ссылок в удалённый репозиторий | `git push [options] [remote] [branch]` | — |
| `git pull` | Получение и интеграция изменений из другого репозитория | `git pull [options] [remote] [branch]` | — |
| `git fetch` | Получение объектов и ссылок из другого репозитория | `git fetch [options] [remote] [refspec...]` | — |
| `git merge` | Объединение историй разработки | `git merge [options] [branch]` | — |
| `git rebase` | Перенос коммитов на другую базовую версию | `git rebase [options] [upstream [branch]]` | — |
| `git branch` | Просмотр, создание и удаление веток | `git branch [options] [branch-name]` | — |
| `git checkout` | Переключение веток и восстановление файлов рабочей копии | `git checkout [options] [branch\|commit\|file]` | — |
| `git switch` | Переключение веток современным интерфейсом Git | `git switch [options] <branch>` | — |
| `git log` | Просмотр истории коммитов | `git log [options] [revision range] [-- path]` | — |
| `git rev-list` | Перечисление коммитов для анализа истории и сценариев автоматизации | `git rev-list [options] <commit>...` | — |
| `git diff` | Просмотр различий между коммитами, рабочей копией и индексом | `git diff [options] [commit] [-- path]` | — |
| `git status` | Просмотр состояния рабочей копии | `git status [options]` | — |
| `git stash` | Временное сохранение незакоммиченных изменений | `git stash [command] [options]` | — |
| `git reset` | Сброс HEAD, индекса или рабочей копии к указанному состоянию | `git reset [options] [commit]` | — |
| `git revert` | Создание коммита, отменяющего предыдущие изменения | `git revert [options] <commit>` | — |
| `git cherry-pick` | Применение изменений из выбранных коммитов | `git cherry-pick [options] <commit>...` | — |
| `git tag` | Создание, просмотр, удаление и проверка тегов | `git tag [options] [tagname] [commit]` | — |
| `git remote` | Управление удалёнными репозиториями | `git remote [options] [command]` | — |
| `git bisect` | Поиск коммита с ошибкой методом деления пополам | `git bisect <subcommand> [options]` | — |
| `git blame` | Просмотр авторов и версий последних изменений строк файла | `git blame [options] <file>` | — |
| `git show` | Просмотр коммитов и других объектов Git | `git show [options] <object>` | — |
| `git clean` | Удаление неотслеживаемых файлов рабочей копии | `git clean [options]` | — |
| `git worktree` | Управление несколькими рабочими копиями репозитория | `git worktree <command> [options]` | — |
| `git submodule` | Инициализация, обновление и просмотр подмодулей | `git submodule [command] [options]` | — |
| `git reflog` | Просмотр истории перемещений ссылок Git | `git reflog [options] [ref]` | — |
| `git merge-base` | Поиск общего предка коммитов и веток | `git merge-base [options] <commit> <commit>...` | — |

Пример запроса: `Создать Git-репозиторий в текущей папке`.

Шаблон:

```bash
git init
```

<a id="docker"></a>

## Docker и Kubernetes

Источник: [docker.json](../src/cf/data/commands/docker.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `docker run` | Создание и запуск контейнера из образа | `docker run [options] IMAGE [command] [args]` | — |
| `docker build` | Сборка образа из Dockerfile | `docker build [options] PATH` | — |
| `docker ps` | Просмотр запущенных контейнеров | `docker ps [options]` | — |
| `docker images` | Просмотр локальных образов Docker | `docker images [options] [REPOSITORY[:TAG]]` | — |
| `docker pull` | Загрузка образа из реестра | `docker pull [options] NAME[:TAG\|@DIGEST]` | — |
| `docker push` | Отправка образа в реестр | `docker push [options] NAME[:TAG]` | — |
| `docker exec` | Выполнение команды в работающем контейнере | `docker exec [options] CONTAINER COMMAND [args]` | — |
| `docker logs` | Просмотр журналов контейнера | `docker logs [options] CONTAINER` | — |
| `docker stop` | Остановка работающих контейнеров | `docker stop [options] CONTAINER [CONTAINER...]` | — |
| `docker rm` | Удаление контейнеров | `docker rm [options] CONTAINER [CONTAINER...]` | — |
| `docker rmi` | Удаление образов Docker | `docker rmi [options] IMAGE [IMAGE...]` | — |
| `docker compose` | Описание и запуск приложений из нескольких контейнеров через Docker Compose | `docker compose [options] [command]` | — |
| `docker network` | Управление сетями Docker | `docker network [command] [options]` | — |
| `docker volume` | Управление томами Docker для хранения данных | `docker volume [command] [options]` | — |
| `docker inspect` | Просмотр подробной информации об объектах Docker | `docker inspect [options] NAME\|ID [NAME\|ID...]` | — |
| `docker system` | Управление системными ресурсами Docker | `docker system [command]` | — |
| `kubectl get` | Просмотр ресурсов Kubernetes | `kubectl get [resource] [options]` | — |
| `kubectl apply` | Применение конфигурации Kubernetes из файла или стандартного ввода | `kubectl apply -f FILENAME [options]` | — |
| `kubectl logs` | Просмотр журналов контейнера в pod | `kubectl logs [options] POD [-c CONTAINER]` | — |
| `kubectl exec` | Выполнение команды в контейнере Kubernetes | `kubectl exec [options] POD -- COMMAND [args]` | — |
| `kubectl describe` | Просмотр подробной информации о ресурсах Kubernetes | `kubectl describe [resource] [name] [options]` | — |
| `kubectl port-forward` | Перенаправление локальных портов к pod | `kubectl port-forward [resource] LOCAL_PORT:REMOTE_PORT [options]` | — |

Пример запроса: `Запустить новый контейнер Ubuntu с интерактивным Bash`.

Шаблон:

```bash
docker run -it ubuntu bash
```

<a id="package_managers"></a>

## Менеджеры пакетов

Источник: [package_managers.json](../src/cf/data/commands/package_managers.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `apt` | Управление пакетами в системах на основе Debian | `apt [options] command [package...]` | — |
| `apt-get` | Управление пакетами низкоуровневым интерфейсом APT | `apt-get [options] command [package...]` | — |
| `dpkg` | Управление отдельными пакетами Debian | `dpkg [options] action` | — |
| `yum` | Управление пакетами RHEL, CentOS 7 и более старых систем | `yum [options] command [package...]` | — |
| `dnf` | Управление пакетами Fedora, RHEL 8 и более новых систем | `dnf [options] command [package...]` | — |
| `pacman` | Управление пакетами Arch Linux | `pacman [options] [targets...]` | — |
| `brew` | Установка пакетов Homebrew в macOS и Linux | `brew [command] [options] [formula\|cask...]` | — |
| `snap` | Управление пакетами Snap | `snap [command] [options] [snap...]` | — |
| `flatpak` | Установка приложений Flatpak | `flatpak [command] [options] [app...]` | — |
| `pip` | Установка пакетов Python | `pip [command] [options] [package...]` | — |
| `pip3` | Установка пакетов Python 3 | `pip3 [command] [options] [package...]` | — |
| `npm` | Управление пакетами Node.js | `npm [command] [options] [package...]` | — |
| `npx` | Разовый запуск пакетов npm и генераторов проектов без глобальной установки | `npx [options] <package>[@version] [args]` | — |
| `yarn` | Управление зависимостями JavaScript через Yarn | `yarn [command] [options] [package...]` | — |
| `gem` | Управление пакетами Ruby | `gem [command] [options] [gemname...]` | — |
| `cargo` | Сборка проектов Rust и управление пакетами | `cargo [command] [options]` | — |
| `go install` | Сборка и установка программ Go | `go install [packages]` | — |

Пример запроса: `Обновить списки доступных пакетов APT`.

Шаблон:

```bash
sudo apt update
```

<a id="perl_oneliners"></a>

## Однострочные команды Perl

Источник: [perl_oneliners.json](../src/cf/data/commands/perl_oneliners.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `perl text substitution` | Замена текста и преобразования регулярными выражениями в однострочниках Perl | `perl -pe 's/pattern/replacement/flags' \| perl -pie 's/pattern/replacement/flags' file` | — |
| `perl text extraction` | Извлечение полей, адресов и других фрагментов текста средствами Perl | `perl -ne 'print if /pattern/' \| perl -ne 'print $1 if /regex/'` | — |
| `perl line filtering` | Фильтрация, выбор диапазонов и удаление повторов строк средствами Perl | `perl -ne 'print if condition' \| perl -ne 'print unless condition'` | — |
| `perl text formatting` | Выравнивание, изменение регистра и форматирование текста средствами Perl | `perl -pe 'formatting expression' \| perl -ne 'printf format, fields'` | — |
| `perl csv/data processing` | Разбор CSV и преобразование табличных данных средствами Perl | `perl -F, -ane 'expression' \| perl -MText::CSV -ne 'expression'` | — |
| `perl file operations` | Переименование, просмотр свойств и обработка файлов средствами Perl | `perl -e 'file operation code' \| perl -MFile::Find -e 'code'` | — |
| `perl math and counting` | Подсчёты, статистика и построение гистограмм средствами Perl | `perl -ane 'math expression' \| perl -ne 'counting expression'` | — |
| `perl string manipulation` | Разворот, перекодировка и посимвольная обработка строк средствами Perl | `perl -pe 'string operation' \| perl -e 'encode/decode expression'` | — |
| `perl log analysis` | Разбор журналов, временных меток и подсчёт ошибок средствами Perl | `perl -ne 'log parsing expression' \| perl -ane 'log field extraction'` | — |
| `perl network/sysadmin` | Работа с адресами, конфигурацией и административными задачами средствами Perl | `perl -ne 'sysadmin expression' \| perl -e 'network operation'` | — |
| `perl json/xml processing` | Обработка JSON, XML и других структурированных данных средствами Perl | `perl -MJSON -e 'expression' \| perl -MXML::Simple -e 'expression'` | — |
| `perl date/time` | Преобразование дат, времени и длительностей средствами Perl | `perl -e 'time expression' \| perl -MPOSIX -e 'date formatting'` | — |
| `perl spacing` | Добавление и удаление пустых строк средствами Perl | `perl -pe '...' \| perl -ne '...'` | — |
| `perl numbering` | Нумерация строк и числовое оформление средствами Perl | `perl -pe '...' \| perl -ne '...'` | — |
| `perl calculations` | Математические вычисления и числовые операции средствами Perl | `perl -e '...' \| perl -le '...' \| perl -ne '...'` | — |
| `perl string generation` | Генерация строк, массивов и последовательностей средствами Perl | `perl -e '...' \| perl -le '...'` | — |
| `perl text conversion` | Преобразование текстовых форматов и кодировок средствами Perl | `perl -pe '...' \| perl -le '...'` | — |
| `perl selective printing` | Выборочный вывод строк по шаблонам, позициям и условиям средствами Perl | `perl -ne '...'` | — |
| `perl selective deleting` | Выборочное удаление строк и символов средствами Perl | `perl -ne '...' \| perl -i -ne '...'` | — |
| `perl useful regexes` | Регулярные выражения Perl для адресов, дат и структурированных данных | `perl -ne '...'` | — |
| `perl tricks` | Приёмы Perl с чтением файла целиком, блоками BEGIN/END и специальными переменными | `perl -e '...' \| perl -0777 -e '...'` | — |

Пример запроса: `переставить фамилию и имя из записи Фамилия, Имя`.

Шаблон:

```bash
perl -pe 's/(\w+), (\w+)/$2 $1/' names.txt
```

<a id="macos"></a>

## macOS

Источник: [macos.json](../src/cf/data/commands/macos.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `sw_vers` | Просмотр названия, версии и номера сборки macOS | `sw_vers [-productName\|-productVersion\|-buildVersion]` | — |
| `diskutil` | Управление дисками, томами и контейнерами APFS в macOS | `diskutil [verb] [options]` | — |
| `launchctl` | Управление пользовательскими и системными службами launchd | `launchctl subcommand [arguments]` | — |
| `plutil` | Проверка, изменение и преобразование файлов plist | `plutil [options] file` | — |
| `mdfind` | Поиск файлов через индекс метаданных Spotlight | `mdfind [-live] [query]` | — |
| `mdls` | Просмотр метаданных Spotlight для файла | `mdls [-raw] [-name attr] file` | — |
| `ditto` | Копирование каталогов с сохранением расширенных атрибутов и метаданных macOS | `ditto [options] src ... dst` | — |
| `xattr` | Просмотр и изменение расширенных атрибутов файлов | `xattr [-lrs] [options] file` | — |
| `softwareupdate` | Установка системных обновлений macOS из терминала | `softwareupdate [command] [args]` | — |
| `osascript` | Запуск AppleScript и JavaScript for Automation из оболочки | `osascript [-l language] script` | — |
| `networksetup` | Настройка сетевых подключений, прокси и Wi-Fi в macOS | `networksetup -listallhardwareports \| ...` | — |
| `scutil` | Просмотр системной сетевой конфигурации, DNS и прокси | `scutil [--dns\|--nc\|--proxy]` | — |
| `security` | Управление связками ключей, сертификатами и защищённым хранилищем | `security [command] [options]` | — |
| `codesign` | Подпись и проверка подписи программ и пакетов macOS | `codesign -s identity \| -dv path` | — |
| `log` | Просмотр и отслеживание объединённого журнала macOS | `log [show\|stream\|collect]` | — |
| `pmset` | Управление сном, пробуждением и другими параметрами энергосбережения | `pmset [-g] [command]` | — |
| `system_profiler` | Просмотр аппаратной и программной конфигурации системы | `system_profiler [dataType]` | — |
| `screencapture` | Создание снимков экрана из терминала | `screencapture [options] file` | — |
| `qlmanage` | Управление предпросмотром файлов Quick Look | `qlmanage [options]` | — |

Пример запроса: `Узнать версию установленной macOS`.

Шаблон:

```bash
sw_vers
```

<a id="misc"></a>

## Другие инструменты

Источник: [misc.json](../src/cf/data/commands/misc.json).

| Команда / группа | Что помогает сделать | Синтаксис | Автоподстановка |
|---|---|---|---|
| `echo` | Вывод строки текста в стандартный вывод | `echo [options] [string...]` | — |
| `printf` | Форматированный вывод данных | `printf FORMAT [ARGUMENT...]` | — |
| `read` | Чтение строки стандартного ввода в переменную | `read [options] [name...]` | — |
| `test` | Проверка условий в сценариях оболочки | `test EXPRESSION` | — |
| `expr` | Вычисление арифметических и строковых выражений | `expr EXPRESSION` | — |
| `bc` | Вычисления с произвольной точностью | `bc [options] [file...]` | — |
| `dc` | Калькулятор с обратной польской записью | `dc [options] [file...]` | — |
| `yes` | Непрерывный повтор строки до остановки процесса | `yes [string]` | — |
| `seq` | Генерация последовательности чисел | `seq [options] LAST or seq FIRST LAST or seq FIRST INCREMENT LAST` | — |
| `shuf` | Перемешивание строк и генерация случайных перестановок | `shuf [options] [file]` | — |
| `sleep` | Приостановка выполнения на указанное время | `sleep NUMBER[smhd]` | — |
| `true` | Успешное завершение без выполнения действий с кодом 0 | `true` | — |
| `false` | Завершение без выполнения действий с кодом 1 | `false` | — |
| `xdg-open` | Открытие файла или URL приложением по умолчанию в Linux | `xdg-open file\|URL` | — |
| `open` | Открытие файлов, каталогов и URL в macOS | `open [options] file\|URL\|application` | — |
| `pbcopy` | Копирование стандартного ввода в буфер обмена macOS | `pbcopy` | — |
| `pbpaste` | Вывод содержимого буфера обмена macOS | `pbpaste` | — |
| `defaults` | Просмотр и изменение пользовательских настроек macOS | `defaults [command] [domain [key [value]]]` | — |
| `say` | Преобразование текста в речь в macOS | `say [options] [text]` | — |
| `caffeinate` | Предотвращение перехода macOS в режим сна | `caffeinate [options] [command]` | — |
| `alias` | Создание псевдонимов команд | `alias [name[=value]]` | — |
| `unalias` | Удаление псевдонимов команд | `unalias [-a] [name...]` | — |
| `history` | Просмотр и изменение истории команд | `history [options] [n]` | — |
| `fc` | Просмотр, редактирование и повторный запуск команд из истории | `fc [options] [first] [last]` | — |
| `source` | Выполнение файла в окружении текущей оболочки | `source filename [arguments]` | — |
| `eval` | Интерпретация аргументов как команды оболочки | `eval [arguments]` | — |
| `exec` | Замена текущего процесса оболочки другой программой | `exec [command [arguments]]` | — |
| `trap` | Обработка сигналов и событий оболочки | `trap [command] [signal...]` | — |
| `xclip` | Работа с буфером обмена X11 в Linux | `xclip [options]` | — |
| `jq` | Обработка JSON из командной строки | `jq [options] filter [file...]` | — |
| `yq` | Обработка YAML, XML и TOML из командной строки | `yq [options] expression [file...]` | — |
| `bat` | Просмотр файлов с подсветкой синтаксиса и номерами строк | `bat [options] [file...]` | — |
| `exa` | Просмотр файлов с цветовым оформлением и интеграцией Git | `exa [options] [files...]` | — |
| `eza` | Современная замена ls на основе поддерживаемого форка exa | `eza [options] [files...]` | — |
| `man` | Просмотр руководства по команде | `man [section] name` | — |
| `info` | Чтение документации в формате GNU Info | `info [options] [menu-item...]` | — |
| `help` | Справка по встроенным командам оболочки | `help [pattern]` | — |
| `hash` | Управление кешем путей к исполняемым файлам в оболочке | `hash [options] [name...]` | — |
| `tput` | Запрос возможностей терминала и вывод управляющих последовательностей | `tput [options] capname [parameters]` | — |
| `reset` | Восстановление настроек терминала | `reset` | — |
| `openssl` | Работа с ключами TLS, запросами сертификатов, сертификатами и хешами | `openssl command [options]` | — |
| `base64` | Кодирование и декодирование Base64 | `base64 [options] [infile]` | — |
| `uuidgen` | Генерация случайного UUID | `uuidgen [options]` | — |
| `clear` | Очистка экрана терминала | `clear` | — |
| `claude` | Запуск помощника Claude Code, в том числе в неинтерактивных конвейерах | `claude [options] [prompt]` | — |

Пример запроса: `Напечатать приветствие в терминале`.

Шаблон:

```bash
echo "Hello, world!"
```

## Обновление каталога

Каталог формируется из `src/cf/data/commands/*.json`. После изменения словаря выполните из корня проекта:

```bash
.venv/bin/python scripts/generate_command_catalog.py
.venv/bin/python scripts/generate_command_catalog.py --check
```
