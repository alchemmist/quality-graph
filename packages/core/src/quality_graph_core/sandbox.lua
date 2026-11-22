-- Host closures stay private. The chunk runs in text-only mode with a fresh environment.
local objects, arrays, null = {}, {}, {}
local function object() return setmetatable({}, objects) end
local function array() return setmetatable({}, arrays) end
local function run(code, input, decode, quote)
    local env = {input=input, pairs=pairs, ipairs=ipairs, type=type, tonumber=tonumber,
                 tostring=tostring, assert=assert, error=error, table={insert=table.insert},
                 json={array=array, object=object, null=null, decode=function(s)
                     assert(type(s)=='string', 'json.decode requires a string')
                     return decode(s)
                 end}}
    debug.setmetatable("", {__index={match=string.match, sub=string.sub, lower=string.lower, upper=string.upper}})

    local chunk = assert(load(code, 'adapter', 't', env))
    debug.sethook(function() error('Lua instruction limit exceeded') end, '', 2000000)
    local result = chunk()
    assert(type(result)=='table' and result.reportVersion ~= nil, 'expected a producer report with reportVersion')
    local out, seen, size = {}, {}, 0
    local function emit(s)
        size = size + #s
        assert(size <= 1048576, 'Lua output size limit exceeded')
        out[#out+1] = s
    end
    local function encode(v, depth)
        assert(depth <= 64, 'Lua output nesting limit exceeded')
        if v == null then emit('null'); return end
        local t = type(v)
        if t == 'string' or t == 'boolean' or t == 'number' then
            assert(t ~= 'number' or (v == v and math.abs(v) ~= math.huge), 'invalid JSON number')
            emit(quote(v)); return
        end
        assert(t == 'table' and not seen[v], 'non-JSON or cyclic Lua output')
        seen[v] = true
        local list = getmetatable(v) == arrays or (getmetatable(v) ~= objects and #v > 0)
        emit(list and '[' or '{')
        local n = 0
        for k,item in pairs(v) do
            if n > 0 then emit(',') end
            n = n+1
            if list then
                assert(math.type(k)=='integer' and k>=1 and k<=#v, 'invalid array key')
                encode(v[n], depth+1)
            else
                assert(type(k)=='string', 'invalid JSON object key'); emit(quote(k)); emit(':'); encode(item, depth+1)
            end
        end
        emit(list and ']' or '}'); seen[v] = nil
    end
    encode(result, 0)
    return table.concat(out)
end
return run, object, array, null
