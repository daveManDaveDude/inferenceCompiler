#define UNICODE
#define _UNICODE

#include <windows.h>
#include <float.h>

namespace {

constexpr int DisplayId = 100;
constexpr int ButtonBaseId = 200;
constexpr int WindowWidth = 290;
constexpr int WindowHeight = 390;
constexpr int DisplayChars = 128;
constexpr int DisplaySignificantDigits = 12;
constexpr int ScientificLargeExponent = 12;
constexpr int ScientificSmallExponent = -5;

HWND g_display = nullptr;
HFONT g_displayFont = nullptr;
wchar_t g_current[DisplayChars] = L"0";
double g_stored = 0;
wchar_t g_pendingOp = 0;
double g_lastOperand = 0;
wchar_t g_lastOp = 0;
bool g_startNewNumber = true;
bool g_hasLastOperation = false;
bool g_error = false;

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
    if (g_display) {
        SetWindowTextW(g_display, g_current);
    }
}

void SetError() {
    CopyText(L"ERROR");
    g_stored = 0;
    g_pendingOp = 0;
    g_lastOperand = 0;
    g_lastOp = 0;
    g_startNewNumber = true;
    g_hasLastOperation = false;
    g_error = true;
    UpdateDisplay();
}

bool IsFiniteValue(double value) {
    return value <= DBL_MAX && value >= -DBL_MAX;
}

double AbsValue(double value) {
    return value < 0 ? -value : value;
}

bool TryCurrentValue(double& result) {
    const wchar_t* cursor = g_current;
    bool negative = false;
    double value = 0;
    bool sawDigit = false;

    if (*cursor == L'-') {
        negative = true;
        ++cursor;
    }

    while (IsDigit(*cursor)) {
        const int digit = static_cast<int>(*cursor - L'0');
        if (value > (DBL_MAX - digit) / 10) {
            return false;
        }
        value = (value * 10) + digit;
        sawDigit = true;
        ++cursor;
    }

    if (*cursor == L'.') {
        ++cursor;
        double place = 0.1;
        while (IsDigit(*cursor)) {
            value += static_cast<int>(*cursor - L'0') * place;
            sawDigit = true;
            place *= 0.1;
            ++cursor;
        }
    }

    if (!sawDigit) {
        return false;
    }

    result = negative ? -value : value;
    if (!IsFiniteValue(result)) {
        return false;
    }
    return true;
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

void AppendChar(wchar_t* text, int& pos, wchar_t ch) {
    if (pos < DisplayChars - 1) {
        text[pos] = ch;
        ++pos;
        text[pos] = 0;
    }
}

void AppendDigits(wchar_t* text, int& pos, const int* digits, int first, int last) {
    for (int i = first; i < last; ++i) {
        AppendChar(text, pos, static_cast<wchar_t>(L'0' + digits[i]));
    }
}

void AppendUnsignedInt(wchar_t* text, int& pos, int value) {
    wchar_t temp[16];
    int count = 0;

    do {
        temp[count] = static_cast<wchar_t>(L'0' + (value % 10));
        value /= 10;
        ++count;
    } while (value > 0 && count < 16);

    while (count > 0) {
        --count;
        AppendChar(text, pos, temp[count]);
    }
}

void FormatScientific(bool negative, const int* digits, int digitCount, int exponent) {
    int pos = 0;
    g_current[0] = 0;

    if (negative) {
        AppendChar(g_current, pos, L'-');
    }
    AppendChar(g_current, pos, static_cast<wchar_t>(L'0' + digits[0]));
    if (digitCount > 1) {
        AppendChar(g_current, pos, L'.');
        AppendDigits(g_current, pos, digits, 1, digitCount);
    }
    AppendChar(g_current, pos, L'e');
    AppendChar(g_current, pos, exponent < 0 ? L'-' : L'+');
    AppendUnsignedInt(g_current, pos, exponent < 0 ? -exponent : exponent);
}

void FormatPlain(bool negative, const int* digits, int digitCount, int exponent) {
    int pos = 0;
    g_current[0] = 0;

    if (negative) {
        AppendChar(g_current, pos, L'-');
    }

    if (exponent >= 0) {
        const int wholeDigits = exponent + 1;
        const int copiedDigits = digitCount < wholeDigits ? digitCount : wholeDigits;
        AppendDigits(g_current, pos, digits, 0, copiedDigits);
        for (int i = copiedDigits; i < wholeDigits; ++i) {
            AppendChar(g_current, pos, L'0');
        }
        if (digitCount > wholeDigits) {
            AppendChar(g_current, pos, L'.');
            AppendDigits(g_current, pos, digits, wholeDigits, digitCount);
        }
        return;
    }

    AppendChar(g_current, pos, L'0');
    AppendChar(g_current, pos, L'.');
    for (int i = 0; i < -exponent - 1; ++i) {
        AppendChar(g_current, pos, L'0');
    }
    AppendDigits(g_current, pos, digits, 0, digitCount);
}

void FormatNumber(double value) {
    if (!IsFiniteValue(value)) {
        SetError();
        return;
    }

    if (value == 0) {
        CopyText(L"0");
        return;
    }

    const bool negative = value < 0;
    double magnitude = AbsValue(value);
    int exponent = 0;

    while (magnitude >= 10) {
        magnitude /= 10;
        ++exponent;
    }
    while (magnitude < 1) {
        magnitude *= 10;
        --exponent;
    }

    int digits[DisplaySignificantDigits + 1];
    for (int i = 0; i <= DisplaySignificantDigits; ++i) {
        int digit = static_cast<int>(magnitude);
        if (digit < 0) {
            digit = 0;
        }
        if (digit > 9) {
            digit = 9;
        }
        digits[i] = digit;
        magnitude = (magnitude - digit) * 10;
    }

    if (digits[DisplaySignificantDigits] >= 5) {
        for (int i = DisplaySignificantDigits - 1; i >= 0; --i) {
            ++digits[i];
            if (digits[i] < 10) {
                break;
            }
            digits[i] = 0;
            if (i == 0) {
                digits[0] = 1;
                ++exponent;
                break;
            }
        }
    }

    int digitCount = DisplaySignificantDigits;
    while (digitCount > 1 && digits[digitCount - 1] == 0) {
        --digitCount;
    }

    if (exponent >= ScientificLargeExponent || exponent <= ScientificSmallExponent) {
        FormatScientific(negative, digits, digitCount, exponent);
    } else {
        FormatPlain(negative, digits, digitCount, exponent);
        TrimFractionZeros();
    }
}

void ClearCalculator() {
    CopyText(L"0");
    g_stored = 0;
    g_pendingOp = 0;
    g_lastOperand = 0;
    g_lastOp = 0;
    g_startNewNumber = true;
    g_hasLastOperation = false;
    g_error = false;
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
        g_error = false;
        g_hasLastOperation = false;
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

bool ApplyOperation(wchar_t op, double rhs) {
    double result = 0;

    switch (op) {
    case L'+':
        result = g_stored + rhs;
        break;
    case L'-':
        result = g_stored - rhs;
        break;
    case L'*':
        result = g_stored * rhs;
        break;
    case L'/':
        if (rhs == 0) {
            return false;
        }
        result = g_stored / rhs;
        break;
    default:
        result = rhs;
        break;
    }

    if (!IsFiniteValue(result)) {
        return false;
    }

    g_stored = result;
    return true;
}

void InputOperator(wchar_t op) {
    if (g_error) {
        return;
    }

    if (!g_startNewNumber) {
        double current = 0;
        if (!TryCurrentValue(current)) {
            SetError();
            return;
        }
        if (!ApplyOperation(g_pendingOp, current)) {
            SetError();
            return;
        }
        FormatNumber(g_stored);
        UpdateDisplay();
    }

    g_pendingOp = op;
    g_startNewNumber = true;
    g_hasLastOperation = false;
}

void CalculateResult() {
    if (g_error) {
        return;
    }

    if (g_pendingOp == 0) {
        if (!g_hasLastOperation) {
            return;
        }

        double current = g_stored;
        if (!g_startNewNumber) {
            if (!TryCurrentValue(current)) {
                SetError();
                return;
            }
        }

        g_stored = current;
        if (!ApplyOperation(g_lastOp, g_lastOperand)) {
            SetError();
            return;
        }
        FormatNumber(g_stored);
        g_startNewNumber = true;
        UpdateDisplay();
        return;
    }

    double current = g_stored;
    if (!g_startNewNumber) {
        if (!TryCurrentValue(current)) {
            SetError();
            return;
        }
    }

    const wchar_t completedOp = g_pendingOp;
    const double completedOperand = g_startNewNumber ? g_stored : current;
    if (!ApplyOperation(completedOp, completedOperand)) {
        SetError();
        return;
    }

    FormatNumber(g_stored);
    g_pendingOp = 0;
    g_lastOp = completedOp;
    g_lastOperand = completedOperand;
    g_hasLastOperation = true;
    g_startNewNumber = true;
    UpdateDisplay();
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

    g_displayFont = CreateFontW(
        24,
        0,
        0,
        0,
        FW_NORMAL,
        FALSE,
        FALSE,
        FALSE,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        DEFAULT_QUALITY,
        DEFAULT_PITCH | FF_SWISS,
        L"Segoe UI");
    HFONT font = g_displayFont ? g_displayFont : reinterpret_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
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
        if (g_displayFont) {
            DeleteObject(g_displayFont);
            g_displayFont = nullptr;
        }
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
