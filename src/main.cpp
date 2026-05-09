#define UNICODE
#define _UNICODE

#include <windows.h>

namespace {

constexpr int DisplayId = 100;
constexpr int ButtonBaseId = 200;
constexpr int WindowWidth = 290;
constexpr int WindowHeight = 390;
constexpr int DisplayChars = 128;
constexpr int Scale = 10000;
constexpr int MaxValue = 2147483647;
constexpr int MinValue = -2147483647 - 1;

HWND g_display = nullptr;
wchar_t g_current[DisplayChars] = L"0";
int g_stored = 0;
wchar_t g_pendingOp = 0;
bool g_startNewNumber = true;

bool IsDigit(wchar_t ch) {
    return ch >= L'0' && ch <= L'9';
}

int TextLength(const wchar_t* text) {
    int length = 0;
    while (length < DisplayChars - 1 && text[length] != 0) {
        ++length;
    }
    return length;
}

void CopyText(const wchar_t* text) {
    int i = 0;
    for (; i < DisplayChars - 1 && text[i] != 0; ++i) {
        g_current[i] = text[i];
    }
    g_current[i] = 0;
}

void UpdateDisplay() {
    SetWindowTextW(g_display, g_current);
}

int ClampValue(long long value) {
    if (value > MaxValue) {
        return MaxValue;
    }
    if (value < MinValue) {
        return MinValue;
    }
    return static_cast<int>(value);
}

int AddScaledDigit(int value, int digit, int place) {
    const int addend = digit * place;
    if (value > (MaxValue - addend) / 10 && place == Scale) {
        return MaxValue;
    }
    if (place == Scale) {
        return value * 10 + addend;
    }
    if (value > MaxValue - addend) {
        return MaxValue;
    }
    return value + addend;
}

int CurrentValue() {
    const wchar_t* cursor = g_current;
    bool negative = false;
    int value = 0;
    bool sawDigit = false;

    if (*cursor == L'-') {
        negative = true;
        ++cursor;
    }

    while (IsDigit(*cursor)) {
        value = AddScaledDigit(value, static_cast<int>(*cursor - L'0'), Scale);
        sawDigit = true;
        ++cursor;
    }

    if (*cursor == L'.') {
        ++cursor;
        int place = Scale / 10;
        while (IsDigit(*cursor) && place > 0) {
            value = AddScaledDigit(value, static_cast<int>(*cursor - L'0'), place);
            sawDigit = true;
            place /= 10;
            ++cursor;
        }
    }

    if (!sawDigit) {
        return 0;
    }
    return negative ? -value : value;
}

void TrimFractionZeros() {
    int length = TextLength(g_current);
    while (length > 0 && g_current[length - 1] == L'0') {
        g_current[length - 1] = 0;
        --length;
    }
    if (length > 0 && g_current[length - 1] == L'.') {
        g_current[length - 1] = 0;
    }
}

void FormatNumber(int value) {
    if (value == MinValue) {
        CopyText(L"Overflow");
        return;
    }

    const bool negative = value < 0;
    const int magnitude = negative ? -value : value;
    const int whole = magnitude / Scale;
    const int fraction = magnitude % Scale;

    if (fraction == 0) {
        wsprintfW(g_current, negative ? L"-%d" : L"%d", whole);
        return;
    }

    wsprintfW(g_current, negative ? L"-%d.%04d" : L"%d.%04d", whole, fraction);
    TrimFractionZeros();
}

void ClearCalculator() {
    CopyText(L"0");
    g_stored = 0;
    g_pendingOp = 0;
    g_startNewNumber = true;
    UpdateDisplay();
}

bool ContainsDecimal() {
    for (int i = 0; i < DisplayChars && g_current[i] != 0; ++i) {
        if (g_current[i] == L'.') {
            return true;
        }
    }
    return false;
}

void AppendInput(wchar_t ch) {
    int length = TextLength(g_current);
    if (length >= DisplayChars - 1) {
        return;
    }
    g_current[length] = ch;
    g_current[length + 1] = 0;
}

void InputDigit(wchar_t digit) {
    if (g_startNewNumber) {
        if (digit == L'.') {
            CopyText(L"0.");
        } else {
            g_current[0] = digit;
            g_current[1] = 0;
        }
        g_startNewNumber = false;
        UpdateDisplay();
        return;
    }

    if (digit == L'.') {
        if (!ContainsDecimal()) {
            AppendInput(L'.');
        }
    } else if (g_current[0] == L'0' && g_current[1] == 0) {
        g_current[0] = digit;
    } else {
        AppendInput(digit);
    }

    UpdateDisplay();
}

bool ApplyPendingOperation(int rhs) {
    switch (g_pendingOp) {
    case L'+':
        g_stored = ClampValue(static_cast<long long>(g_stored) + rhs);
        return true;
    case L'-':
        g_stored = ClampValue(static_cast<long long>(g_stored) - rhs);
        return true;
    case L'*':
        g_stored = ClampValue((static_cast<long long>(g_stored) * rhs) / Scale);
        return true;
    case L'/':
        if (rhs == 0) {
            CopyText(L"Cannot divide by zero");
            g_pendingOp = 0;
            g_startNewNumber = true;
            UpdateDisplay();
            return false;
        }
        g_stored = ClampValue((static_cast<long long>(g_stored) * Scale) / rhs);
        return true;
    default:
        g_stored = rhs;
        return true;
    }
}

void InputOperator(wchar_t op) {
    if (!g_startNewNumber) {
        if (!ApplyPendingOperation(CurrentValue())) {
            return;
        }
        FormatNumber(g_stored);
        UpdateDisplay();
    } else {
        g_stored = CurrentValue();
    }

    g_pendingOp = op;
    g_startNewNumber = true;
}

void CalculateResult() {
    if (g_pendingOp == 0) {
        return;
    }

    if (ApplyPendingOperation(CurrentValue())) {
        FormatNumber(g_stored);
        g_pendingOp = 0;
        g_startNewNumber = true;
        UpdateDisplay();
    }
}

void CreateButton(HWND parent, const wchar_t* text, int id, int x, int y, int width, int height) {
    CreateWindowExW(
        0,
        L"BUTTON",
        text,
        WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
        x,
        y,
        width,
        height,
        parent,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
        GetModuleHandleW(nullptr),
        nullptr);
}

void CreateControls(HWND hwnd) {
    g_display = CreateWindowExW(
        WS_EX_CLIENTEDGE,
        L"EDIT",
        L"0",
        WS_CHILD | WS_VISIBLE | ES_RIGHT | ES_READONLY,
        15,
        15,
        245,
        45,
        hwnd,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(DisplayId)),
        GetModuleHandleW(nullptr),
        nullptr);

    HFONT font = reinterpret_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
    SendMessageW(g_display, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);

    struct Button {
        const wchar_t* text;
        int id;
        int col;
        int row;
        int cols;
    };

    const Button buttons[] = {
        {L"C",  ButtonBaseId +  0, 0, 0, 1}, {L"/", ButtonBaseId +  1, 1, 0, 1},
        {L"*",  ButtonBaseId +  2, 2, 0, 1}, {L"-", ButtonBaseId +  3, 3, 0, 1},
        {L"7",  ButtonBaseId +  4, 0, 1, 1}, {L"8", ButtonBaseId +  5, 1, 1, 1},
        {L"9",  ButtonBaseId +  6, 2, 1, 1}, {L"+", ButtonBaseId +  7, 3, 1, 1},
        {L"4",  ButtonBaseId +  8, 0, 2, 1}, {L"5", ButtonBaseId +  9, 1, 2, 1},
        {L"6",  ButtonBaseId + 10, 2, 2, 1}, {L"=", ButtonBaseId + 11, 3, 2, 1},
        {L"1",  ButtonBaseId + 12, 0, 3, 1}, {L"2", ButtonBaseId + 13, 1, 3, 1},
        {L"3",  ButtonBaseId + 14, 2, 3, 1},
        {L"0",  ButtonBaseId + 15, 0, 4, 2}, {L".", ButtonBaseId + 16, 2, 4, 1},
    };

    constexpr int startX = 15;
    constexpr int startY = 80;
    constexpr int buttonW = 55;
    constexpr int buttonH = 45;
    constexpr int gap = 8;

    for (const Button& button : buttons) {
        const int x = startX + button.col * (buttonW + gap);
        const int y = startY + button.row * (buttonH + gap);
        const int width = button.cols * buttonW + (button.cols - 1) * gap;
        CreateButton(hwnd, button.text, button.id, x, y, width, buttonH);
    }
}

void HandleButton(int id) {
    switch (id - ButtonBaseId) {
    case 0:
        ClearCalculator();
        break;
    case 1:
        InputOperator(L'/');
        break;
    case 2:
        InputOperator(L'*');
        break;
    case 3:
        InputOperator(L'-');
        break;
    case 4:
    case 5:
    case 6:
        InputDigit(static_cast<wchar_t>(L'7' + (id - ButtonBaseId - 4)));
        break;
    case 7:
        InputOperator(L'+');
        break;
    case 8:
    case 9:
    case 10:
        InputDigit(static_cast<wchar_t>(L'4' + (id - ButtonBaseId - 8)));
        break;
    case 11:
        CalculateResult();
        break;
    case 12:
    case 13:
    case 14:
        InputDigit(static_cast<wchar_t>(L'1' + (id - ButtonBaseId - 12)));
        break;
    case 15:
        InputDigit(L'0');
        break;
    case 16:
        InputDigit(L'.');
        break;
    }
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    switch (msg) {
    case WM_CREATE:
        CreateControls(hwnd);
        return 0;
    case WM_COMMAND:
        if (LOWORD(wparam) >= ButtonBaseId) {
            HandleButton(LOWORD(wparam));
        }
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
}

int RunCalculator(HINSTANCE instance, int showCommand) {
    const wchar_t className[] = L"BasicWin32Calculator";

    WNDCLASSW wc = {};
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.lpszClassName = className;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);

    RegisterClassW(&wc);

    HWND hwnd = CreateWindowExW(
        0,
        className,
        L"Calculator",
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        WindowWidth,
        WindowHeight,
        nullptr,
        nullptr,
        instance,
        nullptr);

    if (!hwnd) {
        return 0;
    }

    ShowWindow(hwnd, showCommand);
    UpdateWindow(hwnd);

    MSG msg = {};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    return static_cast<int>(msg.wParam);
}

} // namespace

extern "C" void wWinMainCRTStartup() {
    HINSTANCE instance = GetModuleHandleW(nullptr);
    ExitProcess(static_cast<UINT>(RunCalculator(instance, SW_SHOWNORMAL)));
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int showCommand) {
    return RunCalculator(instance, showCommand);
}
