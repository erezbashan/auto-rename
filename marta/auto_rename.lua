plugin {
    id = "com.erezbashan.autorename",
    name = "Auto Rename",
    apiVersion = "2.2"
}

action {
    id = "run",
    name = "Auto Rename",
    menuName = "Auto Rename",
    shortName = "Auto Rename",
    isApplicable = function(context)
        return context.activePane ~= nil and context.activePane.model.hasActiveFiles
    end,
    apply = function(context)
        local activeFiles = context.activePane.model.activeFiles
        if not activeFiles or #activeFiles == 0 then
            return
        end

        local args = { "/Users/erezbashan/workspace/auto-rename/auto_rename.py" }
        for _, file in ipairs(activeFiles) do
            local filePath = nil
            if type(file.path) == "string" then
                filePath = file.path
            elseif file.path and file.path.rawValue then
                filePath = file.path.rawValue
            elseif file.rawPath then
                filePath = file.rawPath
            end
            if filePath then
                table.insert(args, filePath)
            end
        end

        if #args > 1 then
            martax.execute("/opt/homebrew/bin/python3", args)
        end
    end
}

local registerContextMenu = contextMenuItemProvider or (marta and marta.contextMenuItemProvider)
registerContextMenu {
    id = "menu",
    forFiles = function(context)
        return {
            {
                actionId = "com.erezbashan.autorename.run",
                category = "mutators"
            }
        }
    end
}
